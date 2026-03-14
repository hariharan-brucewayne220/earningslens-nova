"""
pdf_processor.py: Extract text, table, and image chunks from a SEC filing PDF/HTML.

Returns a list of chunk dicts:
  {type: "text",  page: int, text: str, bbox: tuple}
  {type: "table", page: int, headers: list, rows: list, text_repr: str}
  {type: "image", page: int, image_bytes: bytes, description: str}
"""

import io
import logging
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)

# Minimum characters for a text block to be worth embedding
MIN_TEXT_LEN = 30
# Maximum characters per text chunk (to stay within embedding token limits)
MAX_TEXT_LEN = 2000


class PDFProcessor:
    """Extract structured chunks from a PDF filing."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_chunks(self, pdf_path: str) -> list[dict]:
        """
        Parse the PDF at pdf_path and return all chunks.

        Chunk types:
          - text:  a paragraph / contiguous text block
          - table: a structured table with headers + rows
          - image: a page rendered as PNG bytes (for chart detection)
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        suffix = path.suffix.lower()

        # If it's an HTML file (some EDGAR filings are .htm), extract text directly
        if suffix in (".htm", ".html"):
            return self._extract_from_html(str(path))

        # Otherwise treat as PDF
        return self._extract_from_pdf(str(path))

    # ------------------------------------------------------------------
    # PDF extraction
    # ------------------------------------------------------------------

    def _extract_from_pdf(self, pdf_path: str) -> list[dict]:
        chunks: list[dict] = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                logger.info("Processing %d pages from %s", total_pages, pdf_path)

                for page_num, page in enumerate(pdf.pages, start=1):
                    # --- Tables first (highest signal) ---
                    table_chunks = self._extract_tables(page, page_num)
                    chunks.extend(table_chunks)

                    # --- Text (excluding table bounding boxes) ---
                    text_chunks = self._extract_text(page, page_num, table_chunks)
                    chunks.extend(text_chunks)

                    # --- Page image (for chart / visual detection) ---
                    # Only render pages that look like they contain charts
                    # (heuristic: low text density relative to page area)
                    if self._page_has_visual_content(page, text_chunks, table_chunks):
                        image_chunk = self._render_page_image(page, page_num)
                        if image_chunk:
                            chunks.append(image_chunk)

        except Exception as exc:
            logger.error("PDF extraction failed for %s: %s", pdf_path, exc)
            raise

        logger.info(
            "Extracted %d chunks from %s (text=%d, table=%d, image=%d)",
            len(chunks),
            pdf_path,
            sum(1 for c in chunks if c["type"] == "text"),
            sum(1 for c in chunks if c["type"] == "table"),
            sum(1 for c in chunks if c["type"] == "image"),
        )
        return chunks

    def _extract_tables(self, page, page_num: int) -> list[dict]:
        """Extract all tables from a PDF page."""
        chunks = []
        try:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue

                # First row as headers
                headers = [str(cell or "").strip() for cell in table[0]]
                rows = []
                for row in table[1:]:
                    rows.append([str(cell or "").strip() for cell in row])

                # Build a text representation for embedding
                text_repr = self._table_to_text(headers, rows)
                if len(text_repr) < MIN_TEXT_LEN:
                    continue

                chunks.append({
                    "type": "table",
                    "page": page_num,
                    "headers": headers,
                    "rows": rows,
                    "text_repr": text_repr,
                    "bbox": None,
                })
        except Exception as exc:
            logger.debug("Table extraction failed on page %d: %s", page_num, exc)

        return chunks

    def _extract_text(self, page, page_num: int, table_chunks: list[dict]) -> list[dict]:
        """Extract text blocks from a page, splitting into manageable chunks."""
        chunks = []
        try:
            text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            if not text.strip():
                return chunks

            # Split into paragraphs
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

            buffer = []
            buffer_len = 0

            for para in paragraphs:
                if len(para) < MIN_TEXT_LEN:
                    continue

                if buffer_len + len(para) > MAX_TEXT_LEN and buffer:
                    # Flush buffer
                    chunks.append({
                        "type": "text",
                        "page": page_num,
                        "text": "\n\n".join(buffer),
                        "bbox": None,
                    })
                    buffer = []
                    buffer_len = 0

                buffer.append(para)
                buffer_len += len(para)

            if buffer:
                chunks.append({
                    "type": "text",
                    "page": page_num,
                    "text": "\n\n".join(buffer),
                    "bbox": None,
                })

        except Exception as exc:
            logger.debug("Text extraction failed on page %d: %s", page_num, exc)

        return chunks

    def _page_has_visual_content(
        self, page, text_chunks: list[dict], table_chunks: list[dict]
    ) -> bool:
        """
        Heuristic: render a page as image if it has images or very sparse text
        (likely a chart / diagram page).
        """
        try:
            # Check if page has image objects
            if page.images:
                return True
            # Low text density suggests a chart page
            total_text = sum(len(c.get("text", "")) for c in text_chunks)
            page_area = (page.width or 1) * (page.height or 1)
            if total_text < 200 and page_area > 50000:
                return True
        except Exception:
            pass
        return False

    def _render_page_image(self, page, page_num: int) -> dict | None:
        """Render a PDF page to PNG bytes using pdfplumber's built-in method."""
        try:
            img = page.to_image(resolution=100)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            return {
                "type": "image",
                "page": page_num,
                "image_bytes": image_bytes,
                "description": f"Page {page_num} visual content (chart/diagram)",
            }
        except Exception as exc:
            logger.debug("Page image render failed on page %d: %s", page_num, exc)
            return None

    # ------------------------------------------------------------------
    # HTML extraction (fallback for .htm filings)
    # ------------------------------------------------------------------

    def _extract_from_html(self, html_path: str) -> list[dict]:
        """
        Extract text and tables from an HTML filing document.
        Falls back to simple text splitting without pdfplumber.
        """
        chunks: list[dict] = []

        try:
            with open(html_path, "rb") as fh:
                raw = fh.read()

            # Try to parse with basic HTML stripping
            text = self._strip_html(raw.decode("utf-8", errors="replace"))

            # Split into paragraphs
            paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) >= MIN_TEXT_LEN]

            buffer: list[str] = []
            buffer_len = 0
            page_num = 1

            for para in paragraphs:
                if buffer_len + len(para) > MAX_TEXT_LEN and buffer:
                    chunks.append({
                        "type": "text",
                        "page": page_num,
                        "text": "\n\n".join(buffer),
                        "bbox": None,
                    })
                    buffer = []
                    buffer_len = 0
                    page_num += 1

                buffer.append(para)
                buffer_len += len(para)

            if buffer:
                chunks.append({
                    "type": "text",
                    "page": page_num,
                    "text": "\n\n".join(buffer),
                    "bbox": None,
                })

        except Exception as exc:
            logger.error("HTML extraction failed for %s: %s", html_path, exc)
            raise

        logger.info(
            "Extracted %d text chunks from HTML: %s", len(chunks), html_path
        )
        return chunks

    @staticmethod
    def _strip_html(html: str) -> str:
        """Very lightweight HTML -> plain text conversion (no external deps)."""
        import re

        # Remove scripts and styles
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I)
        # Replace block-level tags with newlines
        html = re.sub(r"<(p|div|br|tr|li|h[1-6])[^>]*>", "\n", html, flags=re.I)
        # Strip all remaining tags
        html = re.sub(r"<[^>]+>", " ", html)
        # Decode common HTML entities
        html = (
            html.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&nbsp;", " ")
            .replace("&#160;", " ")
            .replace("&quot;", '"')
        )
        # Collapse whitespace
        html = re.sub(r" {2,}", " ", html)
        html = re.sub(r"\n{3,}", "\n\n", html)
        return html.strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _table_to_text(headers: list, rows: list) -> str:
        """Convert a table to a compact text representation for embedding."""
        lines = []
        if headers:
            lines.append(" | ".join(str(h) for h in headers))
            lines.append("-" * min(80, len(lines[0])))
        for row in rows[:50]:  # cap at 50 rows
            lines.append(" | ".join(str(c) for c in row))
        return "\n".join(lines)
