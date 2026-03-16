"""
test_sonic_schema.py — progressive schema probe for Nova Sonic bidirectional stream.

Each test sends a slightly different event sequence and reports what Bedrock returns.
Run with: source .venv/bin/activate && python test_sonic_schema.py
"""

import asyncio
import base64
import json
import logging
import os
import uuid

import boto3
from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
)
from dotenv import load_dotenv
from smithy_aws_core.identity.static import StaticCredentialsResolver
from smithy_core.shapes import ShapeID

load_dotenv()
logging.basicConfig(level=logging.WARNING)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_client(region: str = "us-east-1") -> BedrockRuntimeClient:
    session = boto3.Session(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=region,
    )
    creds = session.get_credentials().get_frozen_credentials()
    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
        region=region,
        aws_credentials_identity_resolver=StaticCredentialsResolver(),
        auth_scheme_resolver=HTTPAuthSchemeResolver(),
        auth_schemes={ShapeID("aws.auth#sigv4"): SigV4AuthScheme(service="bedrock")},
        aws_access_key_id=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        aws_session_token=creds.token,
    )
    return BedrockRuntimeClient(config=config)


async def run_sequence(model_id: str, events: list[str], label: str, region: str = "us-east-1") -> str:
    """Send events, collect first output event key or error message."""
    client = make_client(region)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=model_id)
    )

    output_events = []

    async def _send():
        try:
            for ev in events:
                chunk = InvokeModelWithBidirectionalStreamInputChunk(
                    value=BidirectionalInputPayloadPart(bytes_=ev.encode("utf-8"))
                )
                await stream.input_stream.send(chunk)
        finally:
            # Flush/close input so server knows we're done sending
            close_fn = getattr(stream.input_stream, "close", None)
            if close_fn is not None:
                result = close_fn()
                if asyncio.iscoroutine(result):
                    await result

    async def _receive():
        try:
            _, out = await stream.await_output()
            async for data in out:
                if data is None:
                    continue
                try:
                    raw = data.value.bytes_.decode("utf-8")
                    ev = json.loads(raw).get("event", {})
                    output_events.append(list(ev.keys()))
                    if "completionEnd" in ev or "audioOutput" in ev:
                        break
                except Exception:
                    output_events.append(["<decode error>"])
                    break
        except Exception as exc:
            output_events.append([f"ERROR: {type(exc).__name__}: {exc}"])

    recv = asyncio.create_task(_receive())
    send = asyncio.create_task(_send())
    await send
    await recv

    result = str(output_events)
    status = "✓" if "ERROR" not in result else "✗"
    print(f"  {status} {label}: {result[:120]}")
    return result


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def main():
    p = str(uuid.uuid4())
    c = str(uuid.uuid4())

    SESSION_START = json.dumps({"event": {"sessionStart": {"inferenceConfiguration": {}}}})
    CONN_END = json.dumps({"event": {"connectionEnd": {}}})
    PROMPT_END = json.dumps({"event": {"promptEnd": {"promptName": p}}})

    PROMPT_START_AUDIO = json.dumps({"event": {"promptStart": {
        "promptName": p,
        "audioOutputConfiguration": {
            "mediaType": "audio/lpcm",
            "sampleRateHertz": 16000,
            "sampleSizeBits": 16,
            "channelCount": 1,
            "voiceId": "matthew",
            "encoding": "base64",
            "audioType": "SPEECH",
        },
    }}})

    PROMPT_START_BOTH = json.dumps({"event": {"promptStart": {
        "promptName": p,
        "textOutputConfiguration": {"mediaType": "text/plain"},
        "audioOutputConfiguration": {
            "mediaType": "audio/lpcm",
            "sampleRateHertz": 16000,
            "sampleSizeBits": 16,
            "channelCount": 1,
            "voiceId": "matthew",
            "encoding": "base64",
            "audioType": "SPEECH",
        },
    }}})

    CONTENT_START_USER_INTERACTIVE = json.dumps({"event": {"contentStart": {
        "promptName": p, "contentName": c,
        "type": "TEXT", "role": "USER", "interactive": True,
        "textInputConfiguration": {"mediaType": "text/plain"},
    }}})

    CONTENT_START_USER_NOT_INTERACTIVE = json.dumps({"event": {"contentStart": {
        "promptName": p, "contentName": c,
        "type": "TEXT", "role": "USER", "interactive": False,
        "textInputConfiguration": {"mediaType": "text/plain"},
    }}})

    TEXT_INPUT = json.dumps({"event": {"textInput": {
        "promptName": p, "contentName": c,
        "content": "Analysis complete. 8 claims verified.",
    }}})

    CONTENT_END = json.dumps({"event": {"contentEnd": {"promptName": p, "contentName": c}}})

    # 16ms of silence (256 bytes of zeros at 16kHz 16-bit mono)
    SILENCE_B64 = base64.b64encode(b"\x00" * 256).decode()
    AUDIO_CONTENT_NAME = str(uuid.uuid4())
    AUDIO_START = json.dumps({"event": {"contentStart": {
        "promptName": p, "contentName": AUDIO_CONTENT_NAME,
        "type": "AUDIO", "role": "USER", "interactive": True,
        "audioInputConfiguration": {
            "mediaType": "audio/lpcm",
            "sampleRateHertz": 16000,
            "sampleSizeBits": 16,
            "channelCount": 1,
            "audioType": "SPEECH",
            "encoding": "base64",
        },
    }}})
    AUDIO_INPUT = json.dumps({"event": {"audioInput": {
        "promptName": p, "contentName": AUDIO_CONTENT_NAME,
        "content": SILENCE_B64,
    }}})
    AUDIO_END = json.dumps({"event": {"contentEnd": {"promptName": p, "contentName": AUDIO_CONTENT_NAME}}})

    MODEL_V1 = "amazon.nova-sonic-v1:0"
    MODEL_V2 = "amazon.nova-2-sonic-v1:0"

    print("\n=== Nova Sonic Schema Probe ===\n")

    # --- Test 1: baseline (current impl, v1, audio-only promptStart, interactive=True)
    print("Group A: model v1, audio-only promptStart")
    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + audio-only promptStart + interactive=True")

    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        CONTENT_START_USER_NOT_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + audio-only promptStart + interactive=False")

    print("\nGroup B: model v1, text+audio promptStart")
    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_BOTH,
        CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + both promptStart + interactive=True")

    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_BOTH,
        CONTENT_START_USER_NOT_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + both promptStart + interactive=False")

    print("\nGroup C: model v2, same variants")
    await run_sequence(MODEL_V2, [
        SESSION_START, PROMPT_START_AUDIO,
        CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v2 + audio-only promptStart + interactive=True")

    await run_sequence(MODEL_V2, [
        SESSION_START, PROMPT_START_BOTH,
        CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v2 + both promptStart + interactive=True")

    print("\nGroup D: audio input (silence) alongside text")
    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        AUDIO_START, AUDIO_INPUT, AUDIO_END,
        CONTENT_START_USER_NOT_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + silence audio + text (interactive=False)")

    await run_sequence(MODEL_V2, [
        SESSION_START, PROMPT_START_AUDIO,
        AUDIO_START, AUDIO_INPUT, AUDIO_END,
        CONTENT_START_USER_NOT_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v2 + silence audio + text (interactive=False)")

    print("\nGroup E: sessionStart shape variants")
    await run_sequence(MODEL_V1, [
        # No inferenceConfiguration at all
        json.dumps({"event": {"sessionStart": {}}}),
        PROMPT_START_AUDIO, CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + sessionStart without inferenceConfiguration")

    await run_sequence(MODEL_V1, [
        # inferenceConfiguration with maxTokens
        json.dumps({"event": {"sessionStart": {"inferenceConfiguration": {"maxTokens": 1024}}}}),
        PROMPT_START_AUDIO, CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END, CONN_END,
    ], "v1 + sessionStart with maxTokens=1024")

    print("\nGroup F: end-sequence variants")
    await run_sequence(MODEL_V1, [
        # Skip promptEnd — just connectionEnd
        SESSION_START, PROMPT_START_AUDIO, CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        CONN_END,
    ], "v1 + no promptEnd, just connectionEnd")

    await run_sequence(MODEL_V1, [
        # Skip connectionEnd — just promptEnd
        SESSION_START, PROMPT_START_AUDIO, CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
        PROMPT_END,
    ], "v1 + no connectionEnd, just promptEnd")

    await run_sequence(MODEL_V1, [
        # Skip both end events — just close stream
        SESSION_START, PROMPT_START_AUDIO, CONTENT_START_USER_INTERACTIVE, TEXT_INPUT, CONTENT_END,
    ], "v1 + no end events at all")

    print("\nGroup G: contentStart field variants")
    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        # No interactive field
        json.dumps({"event": {"contentStart": {"promptName": p, "contentName": c,
            "type": "TEXT", "role": "USER",
            "textInputConfiguration": {"mediaType": "text/plain"}}}}),
        TEXT_INPUT, CONTENT_END, PROMPT_END, CONN_END,
    ], "v1 + no interactive field")

    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        # No textInputConfiguration
        json.dumps({"event": {"contentStart": {"promptName": p, "contentName": c,
            "type": "TEXT", "role": "USER", "interactive": True}}}),
        TEXT_INPUT, CONTENT_END, PROMPT_END, CONN_END,
    ], "v1 + no textInputConfiguration")

    await run_sequence(MODEL_V1, [
        SESSION_START, PROMPT_START_AUDIO,
        # role ASSISTANT instead of USER
        json.dumps({"event": {"contentStart": {"promptName": p, "contentName": c,
            "type": "TEXT", "role": "ASSISTANT", "interactive": True,
            "textInputConfiguration": {"mediaType": "text/plain"}}}}),
        TEXT_INPUT, CONTENT_END, PROMPT_END, CONN_END,
    ], "v1 + role=ASSISTANT")

    print()


asyncio.run(main())
