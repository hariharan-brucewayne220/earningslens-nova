"""
Standalone Nova 2 Sonic repro based on the official AWS sample structure.

Usage:
  source .venv/bin/activate
  python backend/briefing/nova_sonic_sample_repro.py \
    --text "Analysis complete. 8 claims verified. 1 flagged." \
    --output /tmp/nova_sonic_sample.wav
"""

import argparse
import asyncio
import base64
import inspect
import json
import logging
import os
import uuid
import wave

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-2-sonic-v1:0"
REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2


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
                    "voiceId": "matthew",
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
                "role": role,
                "type": "TEXT",
                "interactive": interactive,
                "textInputConfiguration": {"mediaType": "text/plain"},
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


async def _send_json_event(stream, event_json: str) -> None:
    event = InvokeModelWithBidirectionalStreamInputChunk(
        value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
    )
    await stream.input_stream.send(event)


async def run(text: str, output_path: str) -> None:
    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    client = BedrockRuntimeClient(config=config)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
    )

    prompt_name = str(uuid.uuid4())
    system_content_name = str(uuid.uuid4())
    user_content_name = str(uuid.uuid4())
    system_prompt = (
        "You are a warm, professional, and helpful assistant. "
        "Read the user's text clearly and naturally."
    )

    audio_chunks: list[bytes] = []
    completion_event = asyncio.Event()

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
                    logger.info("textOutput: %s", event["textOutput"].get("content", ""))
                elif "audioOutput" in event:
                    content = event["audioOutput"].get("content")
                    if content:
                        audio_chunks.append(base64.b64decode(content))
                elif "completionEnd" in event:
                    logger.info("completionEnd: %s", event["completionEnd"])
                    completion_event.set()
                    break
                else:
                    logger.info("event keys: %s", list(event.keys()))
        except ValidationException as exc:
            raise RuntimeError(f"Nova Sonic validation error: {exc.message or exc}") from exc

    receiver = asyncio.create_task(receive())

    try:
        init_events = [
            _session_start_event(),
            _prompt_start_event(prompt_name),
            _content_start_event(prompt_name, system_content_name, "SYSTEM", False),
            _text_input_event(prompt_name, system_content_name, system_prompt),
            _content_end_event(prompt_name, system_content_name),
        ]
        for event_json in init_events:
            await _send_json_event(stream, event_json)

        await _send_json_event(stream, _content_start_event(prompt_name, user_content_name, "USER", True))
        await _send_json_event(stream, _text_input_event(prompt_name, user_content_name, text))
        await _send_json_event(stream, _content_end_event(prompt_name, user_content_name))
        await _send_json_event(stream, _prompt_end_event(prompt_name))
        logger.info("All prompt events sent")

        await asyncio.wait_for(completion_event.wait(), timeout=30)
        await _send_json_event(stream, _session_end_event())
        logger.info("sessionEnd sent")
    finally:
        close_fn = getattr(stream.input_stream, "close", None)
        if close_fn is not None:
            result = close_fn()
            if inspect.isawaitable(result):
                await result
        try:
            await receiver
        finally:
            if not receiver.done():
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

    if not audio_chunks:
        raise RuntimeError("Nova Sonic returned no audio output")

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(OUTPUT_SAMPLE_RATE)
        wf.writeframes(b"".join(audio_chunks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.text, args.output))


if __name__ == "__main__":
    main()
