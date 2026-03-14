"""
sonic_tts.py: Text-to-speech synthesis for EarningsLens briefings.

Attempts Nova 2 Sonic first; falls back to Amazon Polly (neural, Joanna voice)
if Nova Sonic is unavailable or returns an error.
"""

import json
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_SONIC_MODEL = "amazon.nova-sonic-v1:0"
POLLY_VOICE_ID = "Joanna"
POLLY_ENGINE = "neural"


class SonicTTS:
    """
    TTS synthesiser with Nova 2 Sonic primary and Amazon Polly fallback.

    Usage::

        tts = SonicTTS()
        path = tts.synthesize("Hello world.", "/tmp/out.mp3")
    """

    def __init__(self):
        region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = boto3.client("bedrock-runtime", region_name=region)
        self._polly = boto3.client("polly", region_name=region)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(self, text: str, output_path: str) -> str:
        """
        Synthesise *text* to audio, saving at *output_path*.

        Tries Nova 2 Sonic first; falls back to Amazon Polly on failure.

        Args:
            text: plain-text content to speak
            output_path: local file path to write (should end in .mp3)

        Returns:
            The resolved output path string.
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        result = self._try_nova_sonic(text, output_path)
        if result is not None:
            logger.info("TTS completed via Nova 2 Sonic: %s", output_path)
            return result

        logger.info("Nova Sonic unavailable — using Polly fallback")
        return self._fallback_polly(text, output_path)

    # ------------------------------------------------------------------
    # Nova 2 Sonic
    # ------------------------------------------------------------------

    def _try_nova_sonic(self, text: str, output_path: str) -> str | None:
        """
        Attempt synthesis via amazon.nova-sonic-v1:0 using invoke_model.

        Nova Sonic uses a streaming bidirectional WebSocket in its full form,
        but we first attempt a basic invoke_model call to see if the model
        is accessible in this region/account.  If the call fails for any
        reason (model not found, streaming required, etc.) we return None
        to trigger the Polly fallback.

        Returns the output path on success, or None on any failure.
        """
        try:
            body = json.dumps({
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": text}],
                    }
                ],
                "inferenceConfig": {
                    "maxTokens": 1024,
                },
            })
            response = self._bedrock.invoke_model(
                modelId=NOVA_SONIC_MODEL,
                body=body,
            )
            raw = response["body"].read()
            result = json.loads(raw)

            # Nova Sonic returns audio as base64 in the response body
            # Attempt to extract audio bytes from the response
            audio_b64 = None

            # Try common response shapes
            if isinstance(result, dict):
                # Shape 1: {"audioOutput": {"content": "<b64>"}}
                audio_b64 = (
                    result.get("audioOutput", {}).get("content")
                    or result.get("audio", {}).get("data")
                    or result.get("audio_data")
                )

            if audio_b64:
                import base64
                audio_bytes = base64.b64decode(audio_b64)
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)
                return output_path

            # If we got a response but no audio content, model may be text-only
            logger.warning(
                "Nova Sonic response had no audio content (keys=%s); falling back to Polly",
                list(result.keys()) if isinstance(result, dict) else type(result),
            )
            return None

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            logger.warning(
                "Nova Sonic ClientError (%s) — will use Polly fallback: %s",
                error_code,
                exc,
            )
            return None
        except (BotoCoreError, Exception) as exc:  # noqa: BLE001
            logger.warning("Nova Sonic failed (%s) — will use Polly fallback", exc)
            return None

    # ------------------------------------------------------------------
    # Amazon Polly fallback
    # ------------------------------------------------------------------

    def _fallback_polly(self, text: str, output_path: str) -> str:
        """
        Synthesise using Amazon Polly with the neural engine and Joanna voice.

        Raises RuntimeError if Polly also fails, so the caller can surface
        the error cleanly.
        """
        try:
            response = self._polly.synthesize_speech(
                Text=text,
                VoiceId=POLLY_VOICE_ID,
                OutputFormat="mp3",
                Engine=POLLY_ENGINE,
            )
            audio_stream = response.get("AudioStream")
            if audio_stream is None:
                raise RuntimeError("Polly returned no AudioStream")

            with open(output_path, "wb") as f:
                f.write(audio_stream.read())

            logger.info("TTS completed via Polly: %s", output_path)
            return output_path

        except ClientError as exc:
            logger.error("Polly synthesis failed: %s", exc)
            raise RuntimeError(f"Polly TTS failed: {exc}") from exc
        except (BotoCoreError, OSError) as exc:
            logger.error("Polly synthesis error: %s", exc)
            raise RuntimeError(f"Polly TTS error: {exc}") from exc
