"""
AudioIngestor: uploads audio files to S3 for EarningsLens sessions.
"""
import os
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_FORMATS = {".mp3", ".wav", ".m4a"}
S3_BUCKET = os.getenv("S3_BUCKET", "earningslens-demo")


class AudioIngestor:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        self.bucket = S3_BUCKET

    def upload_audio(self, file_path: str, session_id: str) -> str:
        """
        Upload a local audio file to S3.

        Args:
            file_path: Local path to the audio file.
            session_id: Session UUID used to namespace the S3 key.

        Returns:
            S3 URI in the form s3://<bucket>/audio/<session_id>/<filename>

        Raises:
            ValueError: If the file extension is not supported.
            FileNotFoundError: If the local file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported audio format '{ext}'. "
                f"Supported formats: {sorted(SUPPORTED_FORMATS)}"
            )

        s3_key = f"audio/{session_id}/{path.name}"
        self.s3.upload_file(str(path), self.bucket, s3_key)
        s3_uri = f"s3://{self.bucket}/{s3_key}"
        return s3_uri

    def upload_audio_bytes(self, file_bytes: bytes, filename: str, session_id: str) -> str:
        """
        Upload audio from bytes (e.g. from a FastAPI UploadFile).

        Args:
            file_bytes: Raw bytes of the audio file.
            filename: Original filename (used to determine extension and S3 key).
            session_id: Session UUID used to namespace the S3 key.

        Returns:
            S3 URI in the form s3://<bucket>/audio/<session_id>/<filename>

        Raises:
            ValueError: If the file extension is not supported.
        """
        path = Path(filename)
        ext = path.suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported audio format '{ext}'. "
                f"Supported formats: {sorted(SUPPORTED_FORMATS)}"
            )

        s3_key = f"audio/{session_id}/{path.name}"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body=file_bytes,
        )
        s3_uri = f"s3://{self.bucket}/{s3_key}"
        return s3_uri
