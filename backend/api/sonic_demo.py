"""
sonic_demo.py — Standalone Nova 2 Sonic speech-to-speech MVP.

POST /api/sonic-demo/chat   — accepts audio file, returns WAV audio response
GET  /api/sonic-demo/health — sanity check
"""

import asyncio
import base64
import inspect
import io
import json
import logging
import os
import uuid
import wave

from dotenv import load_dotenv
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sonic-demo", tags=["sonic-demo"])

REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
MODEL_ID = "amazon.nova-2-sonic-v1:0"

INPUT_SAMPLE_RATE = 16_000   # Nova Sonic requires 16 kHz input
OUTPUT_SAMPLE_RATE = 24_000  # Nova Sonic outputs 24 kHz
CHUNK_BYTES = 32_768          # PCM chunk size to stream to Sonic
TIMEOUT = 60

SYSTEM_PROMPT = (
    "You are a helpful, friendly AI assistant. "
    "Answer questions concisely and conversationally. "
    "Keep responses under 3 sentences unless asked for more detail."
)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _to_pcm(audio_bytes: bytes, fmt: str) -> bytes:
    """Convert any audio format to 16 kHz 16-bit mono PCM using ffmpeg directly."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as f:
        f.write(audio_bytes)
        input_path = f.name

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_path,
                "-ar", str(INPUT_SAMPLE_RATE),
                "-ac", "1",
                "-f", "s16le",   # raw 16-bit signed little-endian PCM
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
        return result.stdout
    finally:
        import os
        try:
            os.unlink(input_path)
        except OSError:
            pass


def _pcm_to_wav(pcm: bytes, rate: int = OUTPUT_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _detect_format(filename: str, content_type: str) -> str:
    filename = (filename or "").lower()
    ct = (content_type or "").lower()
    if "webm" in filename or "webm" in ct:
        return "webm"
    if filename.endswith(".wav") or "wav" in ct:
        return "wav"
    if filename.endswith(".mp3") or "mpeg" in ct:
        return "mp3"
    if filename.endswith(".m4a") or "mp4" in ct:
        return "mp4"
    return "webm"  # default — browser MediaRecorder


# ---------------------------------------------------------------------------
# Nova Sonic bidirectional stream
# ---------------------------------------------------------------------------

async def _nova_sonic(pcm_input: bytes) -> bytes:
    """Send PCM audio to Nova Sonic; return PCM response bytes."""
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
    from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

    config = Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    client = BedrockRuntimeClient(config=config)

    stream = await asyncio.wait_for(
        client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
        ),
        timeout=TIMEOUT,
    )

    prompt_name = str(uuid.uuid4())
    sys_name = str(uuid.uuid4())
    audio_name = str(uuid.uuid4())

    audio_out: list[bytes] = []
    completion_event = asyncio.Event()
    first_audio_event = asyncio.Event()
    receive_done = asyncio.Event()
    receive_error: Exception | None = None

    async def _send(payload: str) -> None:
        await stream.input_stream.send(
            InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=payload.encode("utf-8"))
            )
        )

    async def _receive() -> None:
        nonlocal receive_error
        try:
            _, out_stream = await stream.await_output()
            async for evt in out_stream:
                if not isinstance(evt, InvokeModelWithBidirectionalStreamOutputChunk):
                    continue
                try:
                    event = json.loads(evt.value.bytes_.decode("utf-8")).get("event", {})
                except Exception:
                    continue
                if "audioOutput" in event:
                    content = event["audioOutput"].get("content", "")
                    if content:
                        audio_out.append(base64.b64decode(content))
                        first_audio_event.set()
                elif "completionEnd" in event:
                    logger.info("Nova Sonic: completionEnd received, %d chunks", len(audio_out))
                    completion_event.set()
                elif "error" in event:
                    raise RuntimeError(f"Nova Sonic error: {event['error']}")
        except ValidationException as exc:
            receive_error = exc
        except Exception as exc:
            receive_error = exc
        finally:
            receive_done.set()

    receive_task = asyncio.create_task(_receive())

    try:
        # --- Session start ---
        await _send(json.dumps({"event": {"sessionStart": {
            "inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}
        }}}))

        # --- Prompt start: declare audio I/O ---
        await _send(json.dumps({"event": {"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "voiceId": "tiffany",
                "encoding": "base64",
                "audioType": "SPEECH",
            },
        }}}))

        # --- System prompt (TEXT) ---
        await _send(json.dumps({"event": {"contentStart": {
            "promptName": prompt_name, "contentName": sys_name,
            "type": "TEXT", "role": "SYSTEM",
            "interactive": False,
            "textInputConfiguration": {"mediaType": "text/plain"},
        }}}))
        await _send(json.dumps({"event": {"textInput": {
            "promptName": prompt_name, "contentName": sys_name,
            "content": SYSTEM_PROMPT,
        }}}))
        await _send(json.dumps({"event": {"contentEnd": {
            "promptName": prompt_name, "contentName": sys_name,
        }}}))

        # --- Audio input (AUDIO) ---
        await _send(json.dumps({"event": {"contentStart": {
            "promptName": prompt_name, "contentName": audio_name,
            "type": "AUDIO", "role": "USER",
            "interactive": True,
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": INPUT_SAMPLE_RATE,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "audioType": "SPEECH",
                "encoding": "base64",
            },
        }}}))

        # Stream PCM in chunks
        for i in range(0, len(pcm_input), CHUNK_BYTES):
            encoded = base64.b64encode(pcm_input[i: i + CHUNK_BYTES]).decode("utf-8")
            await _send(json.dumps({"event": {"audioInput": {
                "promptName": prompt_name, "contentName": audio_name,
                "content": encoded,
            }}}))

        await _send(json.dumps({"event": {"contentEnd": {
            "promptName": prompt_name, "contentName": audio_name,
        }}}))
        await _send(json.dumps({"event": {"promptEnd": {"promptName": prompt_name}}}))

        logger.info("Nova Sonic: all input sent, waiting for first audio...")

        # Consider the request healthy once the model starts speaking.
        await asyncio.wait_for(first_audio_event.wait(), timeout=TIMEOUT)
        logger.info("Nova Sonic: first audio chunk received")

        # completionEnd is useful but not required for a usable response.
        try:
            await asyncio.wait_for(completion_event.wait(), timeout=10)
            await asyncio.sleep(1.5)
        except asyncio.TimeoutError:
            logger.warning("Nova Sonic: completionEnd not received after first audio")

        await _send(json.dumps({"event": {"sessionEnd": {}}}))
        await asyncio.wait_for(receive_done.wait(), timeout=10)

    except asyncio.TimeoutError as exc:
        raise RuntimeError("Nova Sonic timed out waiting for audio output") from exc
    finally:
        close_fn = getattr(stream.input_stream, "close", None)
        if close_fn:
            result = close_fn()
            if inspect.isawaitable(result):
                await result
        await asyncio.gather(receive_task, return_exceptions=True)

    if receive_error:
        raise RuntimeError(f"Nova Sonic stream error: {receive_error}")
    if not audio_out:
        raise RuntimeError("Nova Sonic returned no audio")

    return b"".join(audio_out)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_ID}


@router.post("/chat")
async def sonic_chat(file: UploadFile = File(...)):
    """
    Accept a voice recording, send to Nova 2 Sonic, return WAV audio.
    The browser should send audio/webm (MediaRecorder default).
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio file")

    fmt = _detect_format(file.filename or "", file.content_type or "")
    logger.info("Sonic demo: received %d bytes, fmt=%s", len(raw), fmt)

    # Convert to PCM in thread pool (pydub/ffmpeg is CPU-bound)
    try:
        loop = asyncio.get_event_loop()
        pcm_in = await loop.run_in_executor(None, _to_pcm, raw, fmt)
    except Exception as exc:
        logger.error("Audio conversion error: %s", exc)
        raise HTTPException(status_code=400, detail=f"Audio conversion failed: {exc}")

    logger.info("Sonic demo: PCM input size=%d bytes", len(pcm_in))

    try:
        pcm_out = await _nova_sonic(pcm_in)
    except Exception as exc:
        logger.error("Nova Sonic error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info("Sonic demo: returning %d PCM output bytes", len(pcm_out))
    wav = _pcm_to_wav(pcm_out)
    return Response(content=wav, media_type="audio/wav")
