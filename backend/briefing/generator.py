"""
generator.py: Briefing text generation for EarningsLens post-call analysis.

Uses Nova 2 Lite to produce a concise, natural-speech briefing summarising
claim verification results, and to handle follow-up Q&A questions.
"""

import json
import logging
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_LITE_MODEL = "amazon.nova-lite-v1:0"


class BriefingGenerator:
    """
    Generates spoken briefing text and Q&A responses using Nova 2 Lite.

    All output is plain natural speech — no markdown, no bullet points —
    suitable for direct TTS synthesis.
    """

    BRIEFING_PROMPT = """
You are a precise financial analyst delivering a post-earnings call briefing.
Based on this claim verification data, write a concise spoken briefing (3-4 sentences max).
Format: natural speech, no bullet points, no markdown.

Claims data:
{claims_json}

Include: total verified, total flagged, most important flagged claim with exact figures, forward guidance count.
Example style: "Analysis complete. Of 8 claims reviewed, 7 were verified against the 10-Q. \
One claim was flagged: the CEO stated revenue grew 23 percent year-over-year, \
but the filing shows 18.4 percent growth — a 4.6 percentage point discrepancy. \
4 forward guidance claims remain unverifiable against current filings."
"""

    QA_PROMPT = """
You are a financial analyst assistant answering a follow-up question after an earnings call analysis.

Context:
- Ticker: {ticker}
- Filing date: {filing_date}
- MacroDash summary: {macrodash_summary}

Verified claims data:
{claims_json}

Question: {question}

Answer in 2-3 natural spoken sentences. No markdown, no bullet points.
Be specific — reference actual figures from the claims data when relevant.
"""

    def __init__(self):
        region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = boto3.client("bedrock-runtime", region_name=region)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_briefing_text(self, claims: list[dict]) -> str:
        """
        Generate a natural-speech briefing summarising verification results.

        Args:
            claims: list of verified claim dicts from the pipeline

        Returns:
            Plain text briefing (no markdown), or a fallback string on failure.
        """
        claims_json = self._summarise_claims(claims)
        prompt = self.BRIEFING_PROMPT.format(claims_json=claims_json)
        text = self._call_nova(prompt, max_tokens=300)
        if not text:
            return self._fallback_briefing(claims)
        return text.strip()

    def generate_qa_response(
        self, question: str, claims: list[dict], context: dict
    ) -> str:
        """
        Generate a conversational answer to a follow-up question.

        Args:
            question: the user's spoken/text question
            claims: list of verified claim dicts for the session
            context: dict with keys ticker, filing_date, macrodash_summary

        Returns:
            Plain text response suitable for TTS.
        """
        claims_json = self._summarise_claims(claims)
        prompt = self.QA_PROMPT.format(
            ticker=context.get("ticker", "unknown"),
            filing_date=context.get("filing_date", "unknown"),
            macrodash_summary=context.get("macrodash_summary", "No macro data available."),
            claims_json=claims_json,
            question=question,
        )
        text = self._call_nova(prompt, max_tokens=200)
        if not text:
            return "I'm sorry, I was unable to retrieve an answer at this time."
        return text.strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarise_claims(self, claims: list[dict]) -> str:
        """
        Produce a compact JSON summary of claims suitable for a prompt.

        Trims each claim to its most relevant fields to keep prompt size small.
        """
        summary = []
        for c in claims:
            claim_obj = c.get("claim", c)  # support both wrapped and flat dicts
            summary.append({
                "claim_text": claim_obj.get("claim_text", c.get("claim_text", "")),
                "metric": claim_obj.get("metric", c.get("metric", "")),
                "stated_value": claim_obj.get("value", c.get("stated_value", "")),
                "verdict": c.get("verdict", "UNVERIFIABLE"),
                "confidence": c.get("confidence", 0.0),
                "filing_match": c.get("filing_match"),
                "filing_delta": c.get("filing_delta"),
                "explanation": c.get("explanation", ""),
            })
        return json.dumps(summary, indent=2)

    def _call_nova(self, prompt: str, max_tokens: int = 500) -> str:
        """Invoke Nova 2 Lite and return the raw text response."""
        try:
            response = self._bedrock.invoke_model(
                modelId=NOVA_LITE_MODEL,
                body=json.dumps({
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {"max_new_tokens": max_tokens},
                }),
            )
            result = json.loads(response["body"].read())
            return result["output"]["message"]["content"][0]["text"]
        except (BotoCoreError, ClientError) as exc:
            logger.error("Nova Lite briefing call failed: %s", exc)
            return ""
        except (KeyError, json.JSONDecodeError) as exc:
            logger.error("Unexpected Nova Lite response shape: %s", exc)
            return ""

    @staticmethod
    def _fallback_briefing(claims: list[dict]) -> str:
        """
        Produce a simple rule-based briefing when Nova is unavailable.
        """
        verdicts = [c.get("verdict", "UNVERIFIABLE") for c in claims]
        total = len(verdicts)
        verified = verdicts.count("VERIFIED")
        flagged = verdicts.count("FLAGGED")
        unverifiable = verdicts.count("UNVERIFIABLE")

        flagged_claims = [c for c in claims if c.get("verdict") == "FLAGGED"]
        top_flag = ""
        if flagged_claims:
            fc = flagged_claims[0]
            top_flag = (
                f" The most significant flag: {fc.get('explanation', 'see report for details')}."
            )

        return (
            f"Analysis complete. Of {total} claims reviewed, {verified} were verified "
            f"against the filing. {flagged} claim{'s' if flagged != 1 else ''} "
            f"{'were' if flagged != 1 else 'was'} flagged.{top_flag} "
            f"{unverifiable} forward guidance or unverifiable claim"
            f"{'s' if unverifiable != 1 else ''} could not be confirmed against current filings."
        )
