"""
claim_extractor.py: Extract numerical/directional claims from earnings call transcripts.

Uses Amazon Nova 2 Lite (amazon.nova-lite-v1:0) via Bedrock to parse
structured claim objects from raw transcript text.
"""

import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_LITE_MODEL = "amazon.nova-lite-v1:0"


class ClaimExtractor:
    """
    Extracts financial claims from earnings call transcript segments using
    Amazon Nova 2 Lite.

    Each extracted claim includes the exact quote, metric description,
    stated value, direction, fiscal period, and whether it is forward guidance.
    """

    EXTRACTION_PROMPT = """
You are analyzing an earnings call transcript segment. Extract ALL numerical or directional claims made by the CEO or CFO.

Return a JSON array. Each element:
{{
  "claim_text": "exact quote from transcript",
  "metric": "what is being claimed (e.g., 'revenue YoY growth', 'gross margin', 'unit deliveries')",
  "value": "the stated value (e.g., '23%', '122%', 'record high')",
  "direction": "up/down/flat/unknown",
  "period": "fiscal period referenced (e.g., 'Q3 FY2025', 'YoY', 'QoQ')",
  "is_forward_guidance": true/false
}}

Only include concrete claims. Reject vague statements. If no claims found, return [].
Transcript segment:
{transcript}
"""

    def __init__(self):
        region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = boto3.client("bedrock-runtime", region_name=region)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_claims(self, transcript_text: str) -> list[dict]:
        """
        Send the transcript to Nova 2 Lite and return a list of claim dicts.

        Each claim dict has: claim_text, metric, value, direction, period,
        is_forward_guidance.

        Malformed claims (missing metric or value) are silently dropped.
        """
        if not transcript_text or not transcript_text.strip():
            return []

        prompt = self.EXTRACTION_PROMPT.format(transcript=transcript_text.strip())

        try:
            response = self._bedrock.invoke_model(
                modelId=NOVA_LITE_MODEL,
                body=json.dumps({
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {"max_new_tokens": 1000},
                }),
            )
            result = json.loads(response["body"].read())
            raw_text = result["output"]["message"]["content"][0]["text"]
        except (BotoCoreError, ClientError) as exc:
            logger.error("Nova Lite invocation failed during claim extraction: %s", exc)
            return []
        except (KeyError, json.JSONDecodeError) as exc:
            logger.error("Unexpected Nova Lite response format: %s", exc)
            return []

        return self._parse_claims(raw_text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_claims(self, raw_text: str) -> list[dict]:
        """
        Extract a JSON array from Nova's response text.

        Nova sometimes wraps output in markdown code blocks; this strips them
        before parsing.
        """
        # Try to extract JSON array from the text (handles markdown code blocks)
        json_text = self._extract_json(raw_text)
        if json_text is None:
            logger.warning("No JSON array found in Nova response: %s", raw_text[:200])
            return []

        try:
            claims = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse claims JSON: %s — raw: %s", exc, json_text[:200])
            return []

        if not isinstance(claims, list):
            logger.warning("Expected JSON array, got: %s", type(claims))
            return []

        valid: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            # Reject claims missing metric or value
            if not claim.get("metric") or not claim.get("value"):
                logger.debug("Dropping malformed claim (missing metric/value): %s", claim)
                continue
            # Normalise defaults for optional fields
            claim.setdefault("claim_text", "")
            claim.setdefault("direction", "unknown")
            claim.setdefault("period", "unknown")
            claim.setdefault("is_forward_guidance", False)
            valid.append(claim)

        logger.info("Extracted %d valid claims from transcript segment", len(valid))
        return valid

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """
        Pull the first JSON array out of a Nova response string.

        Handles markdown fences like ```json ... ``` as well as bare arrays.
        """
        # Strip markdown code block if present
        fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1)

        # Look for a bare JSON array
        array_match = re.search(r"(\[.*?\])", text, re.DOTALL)
        if array_match:
            return array_match.group(1)

        return None
