"""
Standalone smoke test for Amazon Nova 2 Sonic.

Purpose:
  1. Verify AWS credentials are usable from this environment.
  2. Verify Bedrock can open a bidirectional stream to Nova 2 Sonic.
  3. Attempt a reference-style Nova 2 Sonic request and report PASS/FAIL clearly.

Usage:
  source .venv/bin/activate
  python backend/briefing/nova_sonic_smoke_test.py

Optional:
  python backend/briefing/nova_sonic_smoke_test.py --output /tmp/nova_sonic_test.wav

Audio-mode:
  python backend/briefing/nova_sonic_smoke_test.py \
    --input-wav /path/to/16khz_mono_pcm.wav \
    --output /tmp/nova_sonic_audio_reply.wav
"""

import argparse
import asyncio
import base64
import inspect
import json
import logging
import os
import sys
import uuid
import wave
from pathlib import Path

import boto3
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
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-2-sonic-v1:0"
REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
OUTPUT_SAMPLE_RATE = 24_000
INPUT_SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
VOICE_ID = "tiffany"
TIMEOUT_SECONDS = 30
INPUT_CHUNK_MS = 100


def _session_start_event() -> str:
    return json.dumps({
        "event": {
            "sessionStart": {
                "inferenceConfiguration": {
                    "maxTokens": 1024,
                    "topP": 0.9,
                    "temperature": 0.7,
                }
            }
        }
    })


def _prompt_start_event(prompt_name: str) -> str:
    return json.dumps({
        "event": {
            "promptStart": {
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
            }
        }
    })


def _content_start_event(prompt_name: str, content_name: str, role: str, interactive: bool) -> str:
    return json.dumps({
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "type": "TEXT",
                "role": role,
                "interactive": interactive,
                "textInputConfiguration": {"mediaType": "text/plain"},
            }
        }
    })


def _content_start_audio_event(prompt_name: str, content_name: str) -> str:
    return json.dumps({
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "type": "AUDIO",
                "role": "USER",
                "interactive": True,
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": INPUT_SAMPLE_RATE,
                    "sampleSizeBits": 16,
                    "channelCount": CHANNELS,
                    "audioType": "SPEECH",
                    "encoding": "base64",
                },
            }
        }
    })


def _text_input_event(prompt_name: str, content_name: str, text: str) -> str:
    return json.dumps({
        "event": {
            "textInput": {
                "promptName": prompt_name,
                "contentName": content_name,
                "content": text,
            }
        }
    })


def _content_end_event(prompt_name: str, content_name: str) -> str:
    return json.dumps({
        "event": {
            "contentEnd": {
                "promptName": prompt_name,
                "contentName": content_name,
            }
        }
    })


def _prompt_end_event(prompt_name: str) -> str:
    return json.dumps({"event": {"promptEnd": {"promptName": prompt_name}}})


def _session_end_event() -> str:
    return json.dumps({"event": {"sessionEnd": {}}})


def _audio_input_event(prompt_name: str, content_name: str, audio_bytes: bytes) -> str:
    return json.dumps({
        "event": {
            "audioInput": {
                "promptName": prompt_name,
                "contentName": content_name,
                "content": base64.b64encode(audio_bytes).decode("utf-8"),
            }
        }
    })


async def _send_event(stream, event_json: str) -> None:
    event = InvokeModelWithBidirectionalStreamInputChunk(
        value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
    )
    await stream.input_stream.send(event)


def _write_wav(path: str, pcm_data: bytes) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(OUTPUT_SAMPLE_RATE)
        wf.writeframes(pcm_data)


def _read_input_wav(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != CHANNELS:
            raise RuntimeError(f"Input WAV must be mono; got {wf.getnchannels()} channels")
        if wf.getsampwidth() != SAMPLE_WIDTH:
            raise RuntimeError(f"Input WAV must be 16-bit PCM; got sample width {wf.getsampwidth()}")
        if wf.getframerate() != INPUT_SAMPLE_RATE:
            raise RuntimeError(f"Input WAV must be {INPUT_SAMPLE_RATE} Hz; got {wf.getframerate()} Hz")
        return wf.readframes(wf.getnframes())


def check_credentials() -> None:
    session = boto3.Session(region_name=REGION)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    logger.info("AWS credentials OK")
    logger.info("AWS account: %s", identity["Account"])
    logger.info("AWS ARN: %s", identity["Arn"])


async def check_nova_sonic(output_path: str, input_wav: str | None) -> None:
    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    client = BedrockRuntimeClient(config=config)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
    )
    logger.info("Nova Sonic stream opened")

    prompt_name = str(uuid.uuid4())
    system_content_name = str(uuid.uuid4())
    user_content_name = str(uuid.uuid4())
    system_prompt = (
        "You are a concise assistant. "
        "Read the user's message out loud clearly and naturally."
    )
    user_text = "Analysis complete. Eight claims verified. One flagged."

    audio_chunks: list[bytes] = []
    completion_event = asyncio.Event()
    first_audio_event = asyncio.Event()

    async def receive() -> None:
        _, output_stream = await stream.await_output()
        try:
            async for event_data in output_stream:
                if event_data is None:
                    continue
                if not isinstance(event_data, InvokeModelWithBidirectionalStreamOutputChunk):
                    logger.info("Ignoring output event type: %s", type(event_data).__name__)
                    continue

                raw = event_data.value.bytes_.decode("utf-8")
                event = json.loads(raw).get("event", {})

                if "textOutput" in event:
                    text = event["textOutput"].get("content", "")
                    if text:
                        logger.info("textOutput: %s", text)
                elif "audioOutput" in event:
                    content = event["audioOutput"].get("content")
                    if content:
                        audio_chunks.append(base64.b64decode(content))
                        first_audio_event.set()
                elif "completionEnd" in event:
                    logger.info("completionEnd received")
                    completion_event.set()
                    break
                elif "error" in event:
                    raise RuntimeError(f"Nova Sonic returned error event: {event['error']}")
        except ValidationException as exc:
            raise RuntimeError(f"Nova Sonic validation error: {exc.message or exc}") from exc

    receiver = asyncio.create_task(receive())
    try:
        events = [
            _session_start_event(),
            _prompt_start_event(prompt_name),
            _content_start_event(prompt_name, system_content_name, "SYSTEM", True),
            _text_input_event(prompt_name, system_content_name, system_prompt),
            _content_end_event(prompt_name, system_content_name),
        ]
        for event_json in events:
            await _send_event(stream, event_json)

        if input_wav:
            pcm_bytes = _read_input_wav(input_wav)
            chunk_size = INPUT_SAMPLE_RATE * SAMPLE_WIDTH * INPUT_CHUNK_MS // 1000
            await _send_event(stream, _content_start_audio_event(prompt_name, user_content_name))
            for idx in range(0, len(pcm_bytes), chunk_size):
                chunk = pcm_bytes[idx:idx + chunk_size]
                if chunk:
                    await _send_event(stream, _audio_input_event(prompt_name, user_content_name, chunk))
                    await asyncio.sleep(INPUT_CHUNK_MS / 1000)
            await _send_event(stream, _content_end_event(prompt_name, user_content_name))
            logger.info("Nova Sonic audio input events sent")
        else:
            await _send_event(stream, _content_start_event(prompt_name, user_content_name, "USER", True))
            await _send_event(stream, _text_input_event(prompt_name, user_content_name, user_text))
            await _send_event(stream, _content_end_event(prompt_name, user_content_name))
            logger.info("Nova Sonic text input events sent")

        if input_wav:
            try:
                await asyncio.wait_for(first_audio_event.wait(), timeout=TIMEOUT_SECONDS)
                logger.info("Nova Sonic produced audio output")
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"Timed out waiting for first Nova Sonic audioOutput after {TIMEOUT_SECONDS}s"
                ) from exc
        else:
            await _send_event(stream, _prompt_end_event(prompt_name))
            logger.info("Nova Sonic promptEnd sent")

        await _send_event(stream, _prompt_end_event(prompt_name))
        logger.info("Nova Sonic promptEnd sent")

        try:
            await asyncio.wait_for(completion_event.wait(), timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("Nova Sonic did not emit completionEnd within %ss", TIMEOUT_SECONDS)
        await _send_event(stream, _session_end_event())
        logger.info("Nova Sonic sessionEnd sent")
    finally:
        close_fn = getattr(stream.input_stream, "close", None)
        if close_fn is not None:
            result = close_fn()
            if inspect.isawaitable(result):
                await result
        await asyncio.gather(receiver, return_exceptions=True)

    if not audio_chunks:
        raise RuntimeError("Nova Sonic returned no audio output")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    _write_wav(output_path, b"".join(audio_chunks))
    logger.info("Nova Sonic PASS")
    logger.info("Audio written to %s", output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/nova_sonic_smoke_test.wav")
    parser.add_argument("--input-wav")
    args = parser.parse_args()

    try:
        check_credentials()
    except (BotoCoreError, ClientError, Exception) as exc:  # noqa: BLE001
        logger.error("AWS credentials FAILED: %s", exc)
        return 1

    try:
        asyncio.run(check_nova_sonic(args.output, args.input_wav))
    except Exception as exc:  # noqa: BLE001
        logger.error("Nova Sonic FAILED: %s: %s", type(exc).__name__, exc)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
