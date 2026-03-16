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

    @staticmethod
    def _ensure_playwright_libs() -> None:
        """
        On WSL/headless Linux, Playwright needs libnspr4 and libnss3.
        If they are not installed system-wide, we may have them extracted
        under ~/.local/lib/x86_64-linux-gnu — add that dir to LD_LIBRARY_PATH.
        """
        user_lib = os.path.expanduser("~/.local/lib/x86_64-linux-gnu")
        if os.path.isdir(user_lib):
            current = os.environ.get("LD_LIBRARY_PATH", "")
            if user_lib not in current:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{user_lib}:{current}" if current else user_lib
                )
                logger.debug("Added %s to LD_LIBRARY_PATH for Playwright", user_lib)

    def _nova_act_download(self, ticker: str, output_dir: str) -> dict:
        """
        Use Nova Act to navigate EDGAR and download the latest 10-Q/10-K.

        Strategy:
        1. Nova Act navigates to the EDGAR filing list for the ticker and clicks the
           most recent 10-Q.  It then returns the current page URL (the filing index
           page, e.g. .../Archives/edgar/data/{CIK}/{accession}-index.htm).
        2. We parse the accession number and CIK from that reliable URL.
        3. We use the existing REST helpers to derive the correct document download
           URL from the index — avoiding any URL hallucination by the model.
        """
        from nova_act import NovaAct

        api_key = os.getenv("NOVA_ACT_API_KEY")
        if not api_key:
            raise ValueError("NOVA_ACT_API_KEY is not set in the environment")

        # Ensure Playwright can find libnspr4/libnss3 on WSL/headless systems
        self._ensure_playwright_libs()

        logger.info("Nova Act: navigating EDGAR for %s...", ticker)

        index_url: Optional[str] = None
        form_type = "10-Q"

        # Start on the EDGAR company filing search page for the ticker
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
            # Step 1: Navigate to the most recent filing index page
            agent.act(
                f"Click on the most recent 10-Q filing link in the filings table. "
                f"If there is no 10-Q, click the most recent 10-K instead."
            )

            # Step 2: Return the current page URL (the filing index page).
            # We ask for the URL of this page — not of any linked document — so the
            # model reads it directly from the address bar rather than constructing it.
            index_result = agent.act_get(
                "What is the full URL of the current page you are on right now? "
                "Return ONLY the URL, starting with https://www.sec.gov"
            )
            index_url = self._extract_url_from_result(index_result)

            # Detect whether we ended up with a 10-K
            if index_result.response and "10-K" in index_result.response.upper():
                form_type = "10-K"

        logger.info(
            "Nova Act: arrived at filing index for %s — %s", ticker, index_url
        )

        # Fall back to second attempt if no URL was returned
        if not index_url:
            logger.info(
                "Nova Act first attempt did not return an index URL for %s; "
                "trying broader search.",
                ticker,
            )
            with NovaAct(
                starting_page="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
                nova_act_api_key=api_key,
                headless=True,
                tty=False,
            ) as agent2:
                agent2.act(
                    f"In the search box, type the ticker symbol {ticker} and search. "
                    f"Then click the most recent 10-Q filing (or 10-K if none)."
                )
                index_result2 = agent2.act_get(
                    "What is the full URL of the current page you are on right now? "
                    "Return ONLY the URL, starting with https://www.sec.gov"
                )
                index_url = self._extract_url_from_result(index_result2)
                if index_result2.response and "10-K" in index_result2.response.upper():
                    form_type = "10-K"

        if not index_url:
            raise ValueError(
                f"Nova Act could not navigate to a filing index page for {ticker}"
            )

        # ------------------------------------------------------------------
        # Parse the accession number and CIK from the filing index URL so we
        # can build a verified download URL via the REST helper — this avoids
        # any URL hallucination from the model.
        #
        # Expected URL pattern:
        #   https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION}-index.htm
        #   https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&...  (fallback)
        # ------------------------------------------------------------------
        accession, cik_from_url = self._parse_accession_from_index_url(index_url)

        if accession and cik_from_url:
            logger.info(
                "Nova Act: parsed CIK=%s accession=%s from index URL",
                cik_from_url,
                accession,
            )
            # Find a PDF in the filing; fall back to primary document name via REST
            pdf_doc = self._rest_find_pdf_in_filing(cik_from_url, accession)
            if not pdf_doc:
                # Fetch primary doc name from the submissions API
                try:
                    filing_meta = self._rest_get_latest_filing(ticker, form_type)
                    pdf_doc = filing_meta["primary_document"]
                    filing_date_str = filing_meta["filing_date"]
                except Exception:
                    filing_date_str = datetime.utcnow().strftime("%Y-%m-%d")
                    pdf_doc = None
            else:
                filing_date_str = datetime.utcnow().strftime("%Y-%m-%d")

            if pdf_doc:
                filing_url = self._rest_get_filing_pdf_url(
                    cik_from_url, accession, pdf_doc
                )
            else:
                raise ValueError(
                    f"Nova Act navigated to index page but could not determine "
                    f"primary document filename for {ticker}"
                )
        else:
            # The index URL is unusual; hand off to REST entirely
            raise ValueError(
                f"Nova Act returned unexpected index URL format for {ticker}: {index_url}"
            )

        logger.info("Nova Act resolved filing URL for %s: %s", ticker, filing_url)

        # Download the document
        content = self._download_document(filing_url)

        ext = Path(filing_url).suffix.lower() or ".htm"
        if ext not in (".htm", ".html", ".pdf"):
            ext = ".htm"

        safe_date = filing_date_str.replace("-", "")
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
            "filing_date": filing_date_str,
            "local_path": local_path,
            "s3_path": s3_path,
            "filing_url": filing_url,
            "status": "ready",
            "method": "nova_act",
        }

    @staticmethod
    def _parse_accession_from_index_url(url: str):
        """
        Extract (accession_dashed, cik) from an EDGAR Archives index URL.

        Pattern: /Archives/edgar/data/{CIK}/{ACCESSION_NO_DASHES}/{ACCESSION}-index.htm
            e.g. /Archives/edgar/data/1045810/000104581024000123/0001045810-24-000123-index.htm
        Returns (accession_dashed, cik_str) or (None, None).
        """
        # Try to match the Archives URL pattern
        m = re.search(
            r"/Archives/edgar/data/(\d+)/(\d{18})/",
            url,
        )
        if m:
            cik = m.group(1)
            accession_no_dashes = m.group(2)
            # Convert to dashed format: XXXXXXXXXX-YY-ZZZZZZ
            acc = f"{accession_no_dashes[:10]}-{accession_no_dashes[10:12]}-{accession_no_dashes[12:]}"
            return acc, cik

        # Also try the -index.htm filename pattern
        m2 = re.search(
            r"/Archives/edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})-index\.htm",
            url,
        )
        if m2:
            return m2.group(2), m2.group(1)

        return None, None

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
