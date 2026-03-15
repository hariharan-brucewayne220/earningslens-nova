"""
sonic_tts.py: Proven Amazon Nova 2 Sonic speech-to-speech client.

This module is intentionally built around the path we verified live:
  - model: amazon.nova-2-sonic-v1:0
  - bidirectional Bedrock streaming
  - audio input: 16 kHz, mono, 16-bit PCM WAV
  - audio output: 24 kHz, mono, 16-bit PCM WAV

Nova 2 Sonic text-only TTS was not reliable in this environment, so the
working API here is WAV-in/WAV-out rather than text-in/WAV-out.
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
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
INPUT_CHUNK_MS = 100
TIMEOUT_SECONDS = 30
VOICE_ID = "matthew"
DEFAULT_SYSTEM_PROMPT = (
    "You are a concise assistant. "
    "Listen to the user's speech and respond clearly and naturally."
)


class SonicTTS:
    """
    Working Nova 2 Sonic wrapper.

    Proven API:
      - `transceive_wav()` / `transceive_wav_async()`

    Unsupported in this environment:
      - `synthesize()` / `synthesize_async()` text-only TTS
    """

    def __init__(self, *, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self._region = REGION
        self._system_prompt = system_prompt

    def synthesize(self, text: str, output_path: str) -> str:
        """
        Text-only TTS is intentionally disabled.

        We verified that Nova 2 Sonic works with speech-to-speech in this
        environment, but the text-only request path did not complete reliably.
        """
        raise RuntimeError(
            "Nova 2 Sonic text-only TTS is not supported here. "
            "Use transceive_wav(...) with a 16 kHz mono 16-bit PCM WAV input."
        )

    async def synthesize_async(self, text: str, output_path: str) -> str:
        """Async form of synthesize(); intentionally unsupported."""
        raise RuntimeError(
            "Nova 2 Sonic text-only TTS is not supported here. "
            "Use transceive_wav_async(...) with a 16 kHz mono 16-bit PCM WAV input."
        )

    def transceive_wav(self, input_wav_path: str, output_wav_path: str) -> str:
        """Send a WAV file to Nova 2 Sonic and write the spoken reply as WAV."""
        Path(output_wav_path).parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._run_wav(input_wav_path, output_wav_path))
        return output_wav_path

    async def transceive_wav_async(self, input_wav_path: str, output_wav_path: str) -> str:
        """Async version of transceive_wav()."""
        Path(output_wav_path).parent.mkdir(parents=True, exist_ok=True)
        await self._run_wav(input_wav_path, output_wav_path)
        return output_wav_path

    async def _run_wav(self, input_wav_path: str, output_wav_path: str) -> None:
        audio_bytes = _read_input_wav(input_wav_path)

        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self._region}.amazonaws.com",
            region=self._region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        client = BedrockRuntimeClient(config=config)
        stream = await client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=NOVA_SONIC_MODEL)
        )

        prompt_name = str(uuid.uuid4())
        system_content_name = str(uuid.uuid4())
        user_content_name = str(uuid.uuid4())
        audio_chunks: list[bytes] = []
        first_audio_event = asyncio.Event()
        completion_event = asyncio.Event()

        async def _send_event(event_json: str) -> None:
            event = InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
            )
            await stream.input_stream.send(event)

        async def _receive() -> None:
            _, output_stream = await stream.await_output()
            try:
                async for event_data in output_stream:
                    if event_data is None:
                        continue
                    if not isinstance(event_data, InvokeModelWithBidirectionalStreamOutputChunk):
                        logger.debug("Nova Sonic: ignoring output event type %s", type(event_data).__name__)
                        continue

                    try:
                        raw = event_data.value.bytes_.decode("utf-8")
                        event = json.loads(raw).get("event", {})
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Nova Sonic: could not decode output event: %s", exc)
                        continue

                    if "textOutput" in event:
                        text = event["textOutput"].get("content", "")
                        if text:
                            logger.info("Nova Sonic textOutput: %s", text)
                    elif "audioOutput" in event:
                        content = event["audioOutput"].get("content")
                        if content:
                            audio_chunks.append(base64.b64decode(content))
                            first_audio_event.set()
                    elif "completionEnd" in event:
                        logger.info("Nova Sonic: completionEnd received")
                        completion_event.set()
                        break
                    elif "error" in event:
                        raise RuntimeError(f"Nova Sonic error event: {event['error']}")
            except ValidationException as exc:
                raise RuntimeError(f"Nova Sonic validation error: {exc.message or exc}") from exc

        receive_task = asyncio.create_task(_receive())

        try:
            init_events = _build_init_events(
                prompt_name=prompt_name,
                system_content_name=system_content_name,
                system_prompt=self._system_prompt,
            )
            for event_json in init_events:
                await _send_event(event_json)

            await _send_event(_content_start_audio_event(prompt_name, user_content_name))
            chunk_size = INPUT_SAMPLE_RATE * SAMPLE_WIDTH * INPUT_CHUNK_MS // 1000
            for idx in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[idx:idx + chunk_size]
                if chunk:
                    await _send_event(_audio_input_event(prompt_name, user_content_name, chunk))
                    await asyncio.sleep(INPUT_CHUNK_MS / 1000)
            await _send_event(_content_end_event(prompt_name, user_content_name))
            logger.info("Nova Sonic: audio input events sent")

            await asyncio.wait_for(first_audio_event.wait(), timeout=TIMEOUT_SECONDS)
            logger.info("Nova Sonic: first audioOutput received")

            await _send_event(_prompt_end_event(prompt_name))
            logger.info("Nova Sonic: promptEnd sent")

            try:
                await asyncio.wait_for(completion_event.wait(), timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.info("Nova Sonic: completionEnd not received within %ss", TIMEOUT_SECONDS)

            await _send_event(_session_end_event())
            logger.info("Nova Sonic: sessionEnd sent")
        finally:
            close_fn = getattr(stream.input_stream, "close", None)
            if close_fn is not None:
                result = close_fn()
                if inspect.isawaitable(result):
                    await result
            await asyncio.gather(receive_task, return_exceptions=True)

        if not audio_chunks:
            raise RuntimeError("Nova Sonic returned no audio output")

        _write_output_wav(output_wav_path, b"".join(audio_chunks))
        logger.info("Nova Sonic complete: %s", output_wav_path)


def _build_init_events(*, prompt_name: str, system_content_name: str, system_prompt: str) -> list[str]:
    return [
        json.dumps({
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    }
                }
            }
        }),
        json.dumps({
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
        }),
        json.dumps({
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": system_content_name,
                    "type": "TEXT",
                    "role": "SYSTEM",
                    "interactive": True,
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        }),
        json.dumps({
            "event": {
                "textInput": {
                    "promptName": prompt_name,
                    "contentName": system_content_name,
                    "content": system_prompt,
                }
            }
        }),
        json.dumps({
            "event": {
                "contentEnd": {
                    "promptName": prompt_name,
                    "contentName": system_content_name,
                }
            }
        }),
    ]


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


def _read_input_wav(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != CHANNELS:
            raise RuntimeError(f"Input WAV must be mono; got {wf.getnchannels()} channels")
        if wf.getsampwidth() != SAMPLE_WIDTH:
            raise RuntimeError(f"Input WAV must be 16-bit PCM; got sample width {wf.getsampwidth()}")
        if wf.getframerate() != INPUT_SAMPLE_RATE:
            raise RuntimeError(f"Input WAV must be {INPUT_SAMPLE_RATE} Hz; got {wf.getframerate()} Hz")
        return wf.readframes(wf.getnframes())


def _write_output_wav(path: str, pcm_data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(OUTPUT_SAMPLE_RATE)
        wf.writeframes(pcm_data)
