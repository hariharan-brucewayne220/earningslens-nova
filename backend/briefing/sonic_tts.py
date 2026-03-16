"""
sonic_tts.py: Nova 2 Sonic text-to-speech for post-call briefing.

Sends briefing text to Nova Sonic as a TEXT user turn and collects
the audio output. Used only for the end-of-call briefing summary.
"""

import asyncio
import base64
import inspect
import json
import logging
import os
import uuid
import wave
from pathlib import Path

from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.config import Config
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
    InvokeModelWithBidirectionalStreamOutputChunk,
    ValidationException,
)
from dotenv import load_dotenv
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_SONIC_MODEL = "amazon.nova-2-sonic-v1:0"
REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
TIMEOUT_SECONDS = 30
POST_COMPLETION_WAIT_SECONDS = 2.0
VOICE_ID = "tiffany"

READER_SYSTEM_PROMPT = (
    "You are a professional financial analyst presenting a briefing. "
    "Read the content provided by the user clearly and naturally, "
    "as if delivering it to an audience. Do not add commentary."
)


async def synthesize_text(text: str, output_wav_path: str) -> str:
    """
    Send text to Nova 2 Sonic and write the spoken audio to output_wav_path.
    Returns output_wav_path on success.
    Falls back gracefully — caller should check if file exists.
    """
    Path(output_wav_path).parent.mkdir(parents=True, exist_ok=True)

    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    client = BedrockRuntimeClient(config=config)

    try:
        stream = await asyncio.wait_for(
            client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=NOVA_SONIC_MODEL)
            ),
            timeout=TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Timed out connecting to Nova Sonic after {TIMEOUT_SECONDS}s") from exc

    prompt_name = str(uuid.uuid4())
    system_content_name = str(uuid.uuid4())
    user_content_name = str(uuid.uuid4())
    completion_event = asyncio.Event()
    audio_chunks: list[bytes] = []
    receive_done = asyncio.Event()
    receive_error: Exception | None = None

    async def _send(payload: str) -> None:
        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=payload.encode("utf-8"))
        )
        await stream.input_stream.send(chunk)

    async def _receive() -> None:
        nonlocal receive_error
        try:
            _, output_stream = await stream.await_output()
            async for event_data in output_stream:
                if not isinstance(event_data, InvokeModelWithBidirectionalStreamOutputChunk):
                    continue
                try:
                    raw = event_data.value.bytes_.decode("utf-8")
                    event = json.loads(raw).get("event", {})
                except Exception:
                    continue

                if "audioOutput" in event:
                    content = event["audioOutput"].get("content")
                    if content:
                        audio_chunks.append(base64.b64decode(content))
                elif "completionEnd" in event:
                    logger.info("Nova Sonic briefing: completionEnd received")
                    completion_event.set()
                elif "error" in event:
                    raise RuntimeError(f"Nova Sonic error: {event['error']}")
                else:
                    logger.debug("Nova Sonic briefing event: %s", list(event.keys()))
        except ValidationException as exc:
            receive_error = exc
        except Exception as exc:  # noqa: BLE001
            receive_error = exc
        finally:
            receive_done.set()

    receive_task = asyncio.create_task(_receive())
    close_task: asyncio.Task | None = None

    async def _close_after_completion() -> None:
        try:
            await asyncio.wait_for(completion_event.wait(), timeout=TIMEOUT_SECONDS)
            await asyncio.sleep(POST_COMPLETION_WAIT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("Nova Sonic briefing: completionEnd not received within %ss", TIMEOUT_SECONDS)
        await _send(json.dumps({"event": {"sessionEnd": {}}}))

    try:
        # Session + prompt init
        await _send(json.dumps({"event": {"sessionStart": {"inferenceConfiguration": {"maxTokens": 2048, "topP": 0.9, "temperature": 0.7}}}}))
        await _send(json.dumps({"event": {"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                "sampleSizeBits": 16,
                "channelCount": CHANNELS,
                "voiceId": VOICE_ID,
                "encoding": "base64",
                "audioType": "SPEECH",
            },
        }}}))

        # System prompt
        await _send(json.dumps({"event": {"contentStart": {"promptName": prompt_name, "contentName": system_content_name, "type": "TEXT", "role": "SYSTEM", "interactive": False, "textInputConfiguration": {"mediaType": "text/plain"}}}}))
        await _send(json.dumps({"event": {"textInput": {"promptName": prompt_name, "contentName": system_content_name, "content": READER_SYSTEM_PROMPT}}}))
        await _send(json.dumps({"event": {"contentEnd": {"promptName": prompt_name, "contentName": system_content_name}}}))

        # Briefing text as user turn
        await _send(json.dumps({"event": {"contentStart": {"promptName": prompt_name, "contentName": user_content_name, "type": "TEXT", "role": "USER", "interactive": False, "textInputConfiguration": {"mediaType": "text/plain"}}}}))
        await _send(json.dumps({"event": {"textInput": {"promptName": prompt_name, "contentName": user_content_name, "content": text}}}))
        await _send(json.dumps({"event": {"contentEnd": {"promptName": prompt_name, "contentName": user_content_name}}}))
        await _send(json.dumps({"event": {"promptEnd": {"promptName": prompt_name}}}))

        logger.info("Nova Sonic briefing: text sent, waiting for audio...")
        close_task = asyncio.create_task(_close_after_completion())

        # Wait for receive to finish
        await asyncio.wait_for(receive_done.wait(), timeout=TIMEOUT_SECONDS + POST_COMPLETION_WAIT_SECONDS + 5)

    except asyncio.TimeoutError as exc:
        raise RuntimeError("Nova Sonic briefing timed out waiting for response") from exc
    finally:
        close_fn = getattr(stream.input_stream, "close", None)
        if close_fn is not None:
            result = close_fn()
            if inspect.isawaitable(result):
                await result
        if close_task is not None:
            close_task.cancel()
            await asyncio.gather(close_task, return_exceptions=True)
        await asyncio.gather(receive_task, return_exceptions=True)

    if receive_error:
        raise RuntimeError(f"Nova Sonic briefing failed: {receive_error}")

    if not audio_chunks:
        raise RuntimeError("Nova Sonic returned no audio for briefing")

    _write_output_wav(output_wav_path, b"".join(audio_chunks))
    logger.info("Nova Sonic briefing audio written: %s", output_wav_path)
    return output_wav_path


def _write_output_wav(path: str, pcm_data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(OUTPUT_SAMPLE_RATE)
        wf.writeframes(pcm_data)
