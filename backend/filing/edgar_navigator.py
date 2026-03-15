"""
edgar_navigator.py: Autonomous SEC EDGAR navigator for EarningsLens.

Primary path: Nova Act (browser automation via nova-act SDK)
Fallback path: EDGAR REST API (data.sec.gov) — prefixed with _rest_*
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

    Primary path:  Nova Act (browser automation)
    Fallback path: EDGAR REST API (data.sec.gov)
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
    # Nova Act primary path
    # ------------------------------------------------------------------

    def download_filing(self, ticker: str, output_dir: str = "data") -> dict:
        """
        Full flow: ticker -> locate filing via Nova Act -> download -> save locally.

        Falls back to the EDGAR REST API if Nova Act fails for any reason.

        Returns:
            {
                ticker:       str,
                form_type:    str,
                filing_date:  str,
                local_path:   str,   # absolute path to saved file
                s3_path:      str,   # S3 URI (empty string if upload skipped)
                filing_url:   str,
                status:       str,
                method:       str,   # "nova_act" | "rest_api"
            }
        """
        os.makedirs(output_dir, exist_ok=True)

        try:
            return self._nova_act_download(ticker, output_dir)
        except Exception as exc:
            logger.warning(
                "Nova Act filing retrieval failed for %s (%s). Falling back to REST API.",
                ticker,
                exc,
            )
            return self._rest_download(ticker, output_dir)

    def _nova_act_download(self, ticker: str, output_dir: str) -> dict:
        """Use Nova Act to navigate EDGAR and download the latest 10-Q/10-K."""
        from nova_act import NovaAct

        api_key = os.getenv("NOVA_ACT_API_KEY")
        if not api_key:
            raise ValueError("NOVA_ACT_API_KEY is not set in the environment")

        filing_url: Optional[str] = None
        form_type = "10-Q"

        # First attempt: start on the EDGAR full-text search page for the ticker
        edgar_search_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={ticker}&type=10-Q&dateb=&owner=include&count=5"
        )

        with NovaAct(
            starting_page=edgar_search_url,
            nova_act_api_key=api_key,
            headless=True,
            tty=False,
        ) as agent:
            # Navigate to the most recent filing index page
            agent.act(
                f"Click on the most recent 10-Q filing link in the filings table. "
                f"If there is no 10-Q, click the most recent 10-K instead."
            )

            # Extract the URL of the primary document from the filing index
            result = agent.act_get(
                "Find the primary document in this filing index (the main .htm or .pdf file, "
                "NOT the index file itself). Return its full URL starting with https://www.sec.gov",
            )

            filing_url = self._extract_url_from_result(result)
            # Detect whether we ended up with a 10-K
            if result.response and "10-K" in result.response.upper():
                form_type = "10-K"

        # Second attempt: broader search starting from the EDGAR home page
        if not filing_url:
            logger.info(
                "First Nova Act attempt did not return a URL for %s; trying broader search.",
                ticker,
            )
            with NovaAct(
                starting_page="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
                nova_act_api_key=api_key,
                headless=True,
                tty=False,
            ) as agent2:
                agent2.act(
                    f"Search for the company with ticker symbol {ticker}. "
                    f"Navigate to its filing page. Click the most recent 10-Q filing "
                    f"(or 10-K if no 10-Q is available)."
                )
                result2 = agent2.act_get(
                    "Find the primary document in this filing index (the main .htm or .pdf file). "
                    "Return its full URL starting with https://www.sec.gov"
                )
                filing_url = self._extract_url_from_result(result2)
                if result2.response and "10-K" in result2.response.upper():
                    form_type = "10-K"

        if not filing_url:
            raise ValueError(
                f"Nova Act could not locate a filing document URL for {ticker}"
            )

        logger.info("Nova Act resolved filing URL for %s: %s", ticker, filing_url)

        # Download the document
        content = self._download_document(filing_url)

        ext = Path(filing_url).suffix.lower() or ".htm"
        if ext not in (".htm", ".html", ".pdf"):
            ext = ".htm"

        filing_date = datetime.utcnow().strftime("%Y%m%d")
        filename = f"{ticker}_{form_type.replace('-', '')}_{filing_date}{ext}"
        local_path = str(Path(output_dir) / filename)

        with open(local_path, "wb") as fh:
            fh.write(content)

        logger.info("Saved %d bytes to %s", len(content), local_path)

        # Upload to S3 (best-effort)
        s3_path = ""
        try:
            s3_path = self.upload_to_s3(local_path, ticker, filing_date)
        except Exception as exc:
            logger.warning("S3 upload failed (non-fatal): %s", exc)

        return {
            "ticker": ticker,
            "form_type": form_type,
            "filing_date": filing_date,
            "local_path": local_path,
            "s3_path": s3_path,
            "filing_url": filing_url,
            "status": "ready",
            "method": "nova_act",
        }

    def _extract_url_from_result(self, result) -> Optional[str]:
        """
        Extract a SEC filing document URL from a Nova Act ActGetResult object.

        ActGetResult has:
            result.response       -> str | None   (raw text response from the model)
            result.parsed_response -> JsonValue   (parsed JSON if schema was provided)
        """
        # Prefer parsed_response when it is a plain string URL
        if hasattr(result, "parsed_response") and isinstance(result.parsed_response, str):
            candidate = result.parsed_response.strip()
            if candidate.startswith("https://www.sec.gov") and re.search(
                r"\.(htm|html|pdf)", candidate, re.IGNORECASE
            ):
                return candidate

        # Fall back to scanning the raw text response for any SEC document URL
        text = ""
        if hasattr(result, "response") and result.response:
            text = result.response
        elif not text:
            text = str(result)

        urls = re.findall(r"https?://[^\s'\"<>]+", text)
        sec_doc_urls = [
            u for u in urls
            if "sec.gov" in u and re.search(r"\.(htm|html|pdf)", u, re.IGNORECASE)
        ]
        return sec_doc_urls[0] if sec_doc_urls else None

    # ------------------------------------------------------------------
    # REST API fallback path  (original implementation, renamed _rest_*)
    # ------------------------------------------------------------------

    def _rest_download(self, ticker: str, output_dir: str) -> dict:
        """
        Fallback: Full flow via EDGAR REST API.
        ticker -> CIK -> latest 10-Q/10-K -> download -> save locally.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 1. Resolve filing metadata
        filing = self._rest_get_latest_filing(ticker)
        cik = filing["cik"]
        accession = filing["accession_number"]
        primary_doc = filing["primary_document"]
        filing_date = filing["filing_date"]
        form_type = filing["form_type"]

        logger.info(
            "REST API: Found %s for %s: accession=%s date=%s",
            form_type, ticker, accession, filing_date,
        )

        # 2. Prefer a real PDF if one exists in the filing package
        pdf_doc = self._rest_find_pdf_in_filing(cik, accession)
        doc_to_download = pdf_doc if pdf_doc else primary_doc

        doc_url = self._rest_get_filing_pdf_url(cik, accession, doc_to_download)
        logger.info("Downloading from: %s", doc_url)

        content = self._download_document(doc_url)

        # 3. Save
        ext = Path(doc_to_download).suffix.lower() or ".htm"
        safe_date = filing_date.replace("-", "")
        filename = f"{ticker}_{form_type.replace('-', '')}_{safe_date}{ext}"
        local_path = str(Path(output_dir) / filename)

        with open(local_path, "wb") as fh:
            fh.write(content)

        logger.info("Saved %d bytes to %s", len(content), local_path)

        # Upload to S3 (best-effort)
        s3_path = ""
        try:
            s3_path = self.upload_to_s3(local_path, ticker, safe_date)
        except Exception as exc:
            logger.warning("S3 upload failed (non-fatal): %s", exc)

        return {
            "ticker": ticker,
            "form_type": form_type,
            "filing_date": filing_date,
            "local_path": local_path,
            "s3_path": s3_path,
            "filing_url": doc_url,
            "status": "ready",
            "method": "rest_api",
        }

    def _rest_load_ticker_cik_map(self) -> dict:
        """Download and cache the SEC company_tickers.json mapping."""
        if self._ticker_cik_map is not None:
            return self._ticker_cik_map

        url = "https://www.sec.gov/files/company_tickers.json"
        with httpx.Client(timeout=30, headers=SEC_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        mapping = {}
        for entry in data.values():
            t = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if t:
                mapping[t] = cik

        self._ticker_cik_map = mapping
        logger.info("Loaded %d ticker->CIK mappings from SEC", len(mapping))
        return mapping

    def get_cik(self, ticker: str) -> str:
        """Return the zero-padded 10-digit CIK for a given ticker symbol."""
        mapping = self._rest_load_ticker_cik_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise ValueError(f"Ticker '{ticker}' not found in SEC company_tickers.json")
        return cik

    def get_latest_filing(self, ticker: str, form_type: str = "10-Q") -> dict:
        """Public wrapper kept for backwards-compatibility — delegates to REST helper."""
        return self._rest_get_latest_filing(ticker, form_type)

    def _rest_get_latest_filing(self, ticker: str, form_type: str = "10-Q") -> dict:
        """
        Return metadata for the most recent 10-Q (or 10-K fallback) filing via REST API.
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

        fallback_order = [form_type, "10-K"] if form_type == "10-Q" else [form_type, "10-Q"]

        for target_form in fallback_order:
            for i, form in enumerate(forms):
                if form == target_form:
                    accession_raw = accessions[i].replace("-", "")
                    accession_dashed = (
                        f"{accession_raw[:10]}-{accession_raw[10:12]}-{accession_raw[12:]}"
                    )
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

    def get_filing_pdf_url(self, cik: str, accession_number: str, primary_doc: str) -> str:
        """Public wrapper kept for backwards-compatibility."""
        return self._rest_get_filing_pdf_url(cik, accession_number, primary_doc)

    def _rest_get_filing_pdf_url(
        self, cik: str, accession_number: str, primary_doc: str
    ) -> str:
        """Build the direct SEC URL for the primary filing document."""
        accession_no_dashes = accession_number.replace("-", "")
        cik_int = str(int(cik))
        return f"{SEC_ARCHIVES}/{cik_int}/{accession_no_dashes}/{primary_doc}"

    def _rest_find_pdf_in_filing(
        self, cik: str, accession_number: str
    ) -> Optional[str]:
        """Check the filing index for a PDF document; returns filename or None."""
        accession_no_dashes = accession_number.replace("-", "")
        cik_int = str(int(cik))
        index_url = (
            f"{SEC_ARCHIVES}/{cik_int}/{accession_no_dashes}/{accession_no_dashes}-index.htm"
        )
        try:
            with httpx.Client(timeout=30, headers=SEC_HEADERS, follow_redirects=True) as client:
                resp = client.get(index_url)
                resp.raise_for_status()
                pdf_matches = re.findall(r'href="([^"]+\.pdf)"', resp.text, re.IGNORECASE)
                if pdf_matches:
                    return pdf_matches[0].split("/")[-1]
        except Exception as exc:
            logger.debug("Could not fetch filing index: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Shared download helper
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

    # ------------------------------------------------------------------
    # S3 upload
    # ------------------------------------------------------------------

    def upload_to_s3(self, local_path: str, ticker: str, filing_date: str) -> str:
        """
        Upload a local filing file to S3.

        Returns the S3 URI: s3://{bucket}/filings/{ticker}/{date}{ext}
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
