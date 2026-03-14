"""
TranscribeClient: wraps AWS Transcribe for EarningsLens.

Handles starting jobs, polling status, fetching transcripts, and
parsing transcript JSON into time-stamped segments.
"""
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

import boto3
from dotenv import load_dotenv

load_dotenv()

# Map file extension -> AWS Transcribe media format
EXTENSION_TO_FORMAT: dict[str, str] = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "mp4",
}


class TranscribeClient:
    def __init__(self):
        self.client = boto3.client(
            "transcribe",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_transcription_job(
        self,
        job_name: str,
        s3_uri: str,
        media_format: str | None = None,
    ) -> str:
        """
        Start an AWS Transcribe job.

        Args:
            job_name: Unique job identifier (e.g. "earningslens-<session>-<ts>").
            s3_uri: S3 URI of the audio file (s3://bucket/key).
            media_format: Optional override.  If omitted it is inferred from
                          the file extension in s3_uri.

        Returns:
            job_name (echoed back for convenience).
        """
        if media_format is None:
            media_format = self._infer_media_format(s3_uri)

        self.client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": s3_uri},
            MediaFormat=media_format,
            LanguageCode="en-US",
            Settings={
                "ShowSpeakerLabels": True,
                "MaxSpeakerLabels": 5,
            },
        )
        return job_name

    def poll_transcription_job(self, job_name: str) -> str:
        """
        Return the current status of a Transcribe job.

        Returns:
            One of: 'QUEUED', 'IN_PROGRESS', 'COMPLETED', 'FAILED'
        """
        response = self.client.get_transcription_job(
            TranscriptionJobName=job_name
        )
        return response["TranscriptionJob"]["TranscriptionJobStatus"]

    def get_transcript(self, job_name: str) -> str:
        """
        Fetch the full transcript text for a COMPLETED job.

        Raises:
            RuntimeError: If the job is not yet COMPLETED or has FAILED.
        """
        response = self.client.get_transcription_job(
            TranscriptionJobName=job_name
        )
        job = response["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]

        if status == "FAILED":
            reason = job.get("FailureReason", "unknown reason")
            raise RuntimeError(f"Transcription job '{job_name}' failed: {reason}")
        if status != "COMPLETED":
            raise RuntimeError(
                f"Transcription job '{job_name}' is not complete (status={status})"
            )

        transcript_url = job["Transcript"]["TranscriptFileUri"]
        transcript_json = self._fetch_url(transcript_url)
        return transcript_json["results"]["transcripts"][0]["transcript"]

    def parse_transcript_segments(
        self, transcript_json: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Parse a Transcribe result JSON into time-stamped segments.

        Each segment is a dict: {text, start_time, end_time}

        Args:
            transcript_json: The parsed JSON from the Transcribe output file.

        Returns:
            List of segment dicts ordered by start_time.
        """
        items = transcript_json.get("results", {}).get("items", [])
        segments: list[dict[str, Any]] = []
        current_words: list[str] = []
        current_start: float | None = None
        current_end: float | None = None

        for item in items:
            if item["type"] == "pronunciation":
                word = item["alternatives"][0]["content"]
                start = float(item["start_time"])
                end = float(item["end_time"])

                if current_start is None:
                    current_start = start
                current_end = end
                current_words.append(word)

            elif item["type"] == "punctuation":
                if current_words:
                    # Attach punctuation to last word
                    punct = item["alternatives"][0]["content"]
                    current_words[-1] += punct

                    # End segment on sentence-ending punctuation
                    if punct in {".", "?", "!"}:
                        segments.append(
                            {
                                "text": " ".join(current_words),
                                "start_time": current_start,
                                "end_time": current_end,
                            }
                        )
                        current_words = []
                        current_start = None
                        current_end = None

        # Flush any remaining words
        if current_words and current_start is not None:
            segments.append(
                {
                    "text": " ".join(current_words),
                    "start_time": current_start,
                    "end_time": current_end,
                }
            )

        return segments

    def wait_for_completion(
        self, job_name: str, poll_interval: int = 5, timeout: int = 3600
    ) -> str:
        """
        Block until the job reaches COMPLETED or FAILED.

        Returns:
            Final status string.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.poll_transcription_job(job_name)
            if status in {"COMPLETED", "FAILED"}:
                return status
            time.sleep(poll_interval)
        raise TimeoutError(
            f"Transcription job '{job_name}' did not complete within {timeout}s"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_media_format(s3_uri: str) -> str:
        ext = Path(s3_uri.split("?")[0]).suffix.lower()
        fmt = EXTENSION_TO_FORMAT.get(ext)
        if fmt is None:
            raise ValueError(
                f"Cannot infer media format from extension '{ext}'. "
                f"Supported: {list(EXTENSION_TO_FORMAT.keys())}"
            )
        return fmt

    @staticmethod
    def _fetch_url(url: str) -> dict[str, Any]:
        """Download JSON from a pre-signed URL returned by Transcribe."""
        with urllib.request.urlopen(url) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
