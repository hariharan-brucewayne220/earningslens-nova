"""
edgar_navigator.py: Autonomous SEC EDGAR navigator for EarningsLens.

Uses the EDGAR REST API (no auth required) to:
  - Map ticker -> CIK
  - Find latest 10-Q or 10-K filing
  - Download the primary PDF document
  - Upload to S3
"""

import io
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# EDGAR endpoints
EDGAR_BASE = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

# Polite User-Agent required by SEC
HEADERS = {
    "User-Agent": "EarningsLens/1.0 earningslens@demo.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}

SEC_HEADERS = {
    "User-Agent": "EarningsLens/1.0 earningslens@demo.com",
    "Accept-Encoding": "gzip, deflate",
}


class EDGARNavigator:
    """
    Navigates SEC EDGAR to find and download 10-Q/10-K filings.
    Primary path: EDGAR REST API (data.sec.gov)
    """

    def __init__(self):
        self.s3_bucket = os.environ.get("S3_BUCKET", "earningslens-demo")
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self._s3_client = None
        self._ticker_cik_map: Optional[dict] = None

    @property
    def s3_client(self):
        if self._s3_client is None:
            self._s3_client = boto3.client("s3", region_name=self.aws_region)
        return self._s3_client

    # ------------------------------------------------------------------
    # CIK resolution
    # ------------------------------------------------------------------

    def _load_ticker_cik_map(self) -> dict:
        """Download and cache the SEC company_tickers.json mapping."""
        if self._ticker_cik_map is not None:
            return self._ticker_cik_map

        url = "https://www.sec.gov/files/company_tickers.json"
        with httpx.Client(timeout=30, headers=SEC_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        # data is {0: {cik_str, ticker, title}, 1: ...}
        mapping = {}
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                mapping[ticker] = cik

        self._ticker_cik_map = mapping
        logger.info("Loaded %d ticker->CIK mappings from SEC", len(mapping))
        return mapping

    def get_cik(self, ticker: str) -> str:
        """
        Return the zero-padded 10-digit CIK for a given ticker symbol.

        Raises ValueError if ticker is not found.
        """
        mapping = self._load_ticker_cik_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise ValueError(f"Ticker '{ticker}' not found in SEC company_tickers.json")
        return cik

    # ------------------------------------------------------------------
    # Filing metadata
    # ------------------------------------------------------------------

    def get_latest_filing(self, ticker: str, form_type: str = "10-Q") -> dict:
        """
        Return metadata for the most recent 10-Q (or 10-K fallback) filing.

        Returns:
            {
                accession_number: str,   # e.g. "0001045810-24-000010"
                filing_date:      str,   # e.g. "2024-05-22"
                form_type:        str,   # e.g. "10-Q"
                primary_document: str,   # e.g. "nvda-20240428.htm"
                cik:              str,
            }
        """
        cik = self.get_cik(ticker)
        url = f"{EDGAR_BASE}/submissions/CIK{cik}.json"

        with httpx.Client(timeout=30, headers=HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        dates = filings.get("filingDate", [])
        primary_docs = filings.get("primaryDocument", [])

        # Try the requested form_type first, then fall back
        fallback_order = [form_type, "10-K"] if form_type == "10-Q" else [form_type, "10-Q"]

        for target_form in fallback_order:
            for i, form in enumerate(forms):
                if form == target_form:
                    accession_raw = accessions[i]
                    # normalise: "0001045810-24-000010" (keep dashes)
                    accession = accession_raw.replace("-", "")
                    accession_dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
                    return {
                        "accession_number": accession_dashed,
                        "filing_date": dates[i],
                        "form_type": form,
                        "primary_document": primary_docs[i],
                        "cik": cik,
                    }

        raise ValueError(
            f"No {form_type} or 10-K filings found for ticker '{ticker}' (CIK {cik})"
        )

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def get_filing_pdf_url(self, cik: str, accession_number: str, primary_doc: str) -> str:
        """
        Build the direct SEC URL for the primary filing document.

        accession_number should be dashed: "0001045810-24-000010"
        """
        # Remove dashes for the directory path
        accession_no_dashes = accession_number.replace("-", "")
        # Strip leading zeros from CIK for the archives path
        cik_int = str(int(cik))
        return f"{SEC_ARCHIVES}/{cik_int}/{accession_no_dashes}/{primary_doc}"

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    def _download_document(self, url: str) -> bytes:
        """Download a document from SEC, following redirects."""
        with httpx.Client(
            timeout=120,
            headers=SEC_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content

    def _find_pdf_in_filing(self, cik: str, accession_number: str) -> Optional[str]:
        """
        Check the filing index for a PDF document.
        Returns the filename if a PDF exists, else None.
        """
        accession_no_dashes = accession_number.replace("-", "")
        cik_int = str(int(cik))
        index_url = (
            f"{SEC_ARCHIVES}/{cik_int}/{accession_no_dashes}/{accession_no_dashes}-index.htm"
        )
        try:
            with httpx.Client(timeout=30, headers=SEC_HEADERS, follow_redirects=True) as client:
                resp = client.get(index_url)
                resp.raise_for_status()
                # Look for .pdf links
                text = resp.text
                pdf_matches = re.findall(r'href="([^"]+\.pdf)"', text, re.IGNORECASE)
                if pdf_matches:
                    return pdf_matches[0].split("/")[-1]
        except Exception as exc:
            logger.debug("Could not fetch filing index: %s", exc)
        return None

    def download_filing(self, ticker: str, output_dir: str) -> dict:
        """
        Full flow: ticker -> CIK -> latest 10-Q/10-K -> download -> save locally.

        The primary document is typically an .htm file. We download it as-is;
        if a PDF version exists in the filing index we prefer that.

        Returns:
            {
                ticker:       str,
                form_type:    str,
                filing_date:  str,
                local_path:   str,   # absolute path to saved file
                s3_path:      str,   # S3 URI (empty string if upload skipped)
            }
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 1. Resolve filing metadata
        filing = self.get_latest_filing(ticker)
        cik = filing["cik"]
        accession = filing["accession_number"]
        primary_doc = filing["primary_document"]
        filing_date = filing["filing_date"]
        form_type = filing["form_type"]

        logger.info(
            "Found %s for %s: accession=%s date=%s",
            form_type, ticker, accession, filing_date,
        )

        # 2. Prefer a real PDF if one exists in the filing package
        pdf_doc = self._find_pdf_in_filing(cik, accession)
        if pdf_doc:
            logger.info("Found PDF document in filing: %s", pdf_doc)
            doc_to_download = pdf_doc
        else:
            logger.info("No PDF found; downloading primary document: %s", primary_doc)
            doc_to_download = primary_doc

        doc_url = self.get_filing_pdf_url(cik, accession, doc_to_download)
        logger.info("Downloading from: %s", doc_url)

        content = self._download_document(doc_url)

        # 3. Determine file extension
        ext = Path(doc_to_download).suffix.lower() or ".htm"
        safe_date = filing_date.replace("-", "")
        filename = f"{ticker}_{form_type.replace('-', '')}_{safe_date}{ext}"
        local_path = str(Path(output_dir) / filename)

        with open(local_path, "wb") as fh:
            fh.write(content)

        logger.info("Saved %d bytes to %s", len(content), local_path)

        return {
            "ticker": ticker,
            "form_type": form_type,
            "filing_date": filing_date,
            "local_path": local_path,
            "s3_path": "",
            "doc_url": doc_url,
        }

    # ------------------------------------------------------------------
    # S3 upload
    # ------------------------------------------------------------------

    def upload_to_s3(self, local_path: str, ticker: str, filing_date: str) -> str:
        """
        Upload a local filing file to S3.

        Returns the S3 URI: s3://earningslens-demo/filings/{ticker}/{date}{ext}
        """
        ext = Path(local_path).suffix
        safe_date = filing_date.replace("-", "")
        s3_key = f"filings/{ticker}/{safe_date}{ext}"

        try:
            self.s3_client.upload_file(
                local_path,
                self.s3_bucket,
                s3_key,
                ExtraArgs={"ContentType": "application/pdf"},
            )
            s3_uri = f"s3://{self.s3_bucket}/{s3_key}"
            logger.info("Uploaded to %s", s3_uri)
            return s3_uri
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed: %s", exc)
            raise
