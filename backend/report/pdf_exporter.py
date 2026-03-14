"""
pdf_exporter.py: PDF report generation for EarningsLens using reportlab.

Produces a professional one-page-per-section report with:
  - Header: logo text, ticker, date, summary stats
  - Section: Flagged Claims (red highlight)
  - Section: Verified Claims (green)
  - Section: Unverifiable / Forward Guidance (grey)
  - Footer: "Powered by Amazon Nova | MacroDash"
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from backend.report.json_exporter import generate_json_report

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_RED = colors.HexColor("#C0392B")
_RED_BG = colors.HexColor("#FDEDEC")
_GREEN = colors.HexColor("#1E8449")
_GREEN_BG = colors.HexColor("#EAFAF1")
_GREY = colors.HexColor("#7F8C8D")
_GREY_BG = colors.HexColor("#F2F3F4")
_NAVY = colors.HexColor("#1A2C5B")
_GOLD = colors.HexColor("#F39C12")
_WHITE = colors.white
_BLACK = colors.black


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ELTitle", fontName="Helvetica-Bold", fontSize=22,
            textColor=_NAVY, alignment=TA_CENTER, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ELSubtitle", fontName="Helvetica", fontSize=11,
            textColor=_GREY, alignment=TA_CENTER, spaceAfter=2,
        ),
        "section_header": ParagraphStyle(
            "ELSectionHeader", fontName="Helvetica-Bold", fontSize=13,
            textColor=_WHITE, spaceBefore=10, spaceAfter=4,
        ),
        "claim_title": ParagraphStyle(
            "ELClaimTitle", fontName="Helvetica-Bold", fontSize=10,
            textColor=_BLACK, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "ELBody", fontName="Helvetica", fontSize=9,
            textColor=_BLACK, spaceAfter=2, leading=13,
        ),
        "body_small": ParagraphStyle(
            "ELBodySmall", fontName="Helvetica", fontSize=8,
            textColor=_GREY, spaceAfter=1, leading=11,
        ),
        "footer": ParagraphStyle(
            "ELFooter", fontName="Helvetica-Oblique", fontSize=8,
            textColor=_GREY, alignment=TA_CENTER,
        ),
    }


def _summary_table(summary: dict, styles: dict) -> Table:
    """Build the 4-cell summary stats table."""
    total = summary["total_claims"]
    verified = summary["verified"]
    flagged = summary["flagged"]
    unverifiable = summary["unverifiable"]
    rate = f"{summary['verification_rate'] * 100:.1f}%"

    data = [
        [
            Paragraph(f"<b>{total}</b><br/>Total Claims", styles["body"]),
            Paragraph(f"<b>{verified}</b><br/>Verified", styles["body"]),
            Paragraph(f"<b>{flagged}</b><br/>Flagged", styles["body"]),
            Paragraph(f"<b>{unverifiable}</b><br/>Unverifiable", styles["body"]),
            Paragraph(f"<b>{rate}</b><br/>Verify Rate", styles["body"]),
        ]
    ]
    t = Table(data, colWidths=[1.2 * inch] * 5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EBF5FB")),
        ("BACKGROUND", (1, 0), (1, 0), _GREEN_BG),
        ("BACKGROUND", (2, 0), (2, 0), _RED_BG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, _GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _section_header_table(label: str, bg_color: colors.Color) -> Table:
    """Render a coloured section-header band."""
    t = Table([[label]], colWidths=[7 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (-1, -1), _WHITE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _claim_block(claim: dict, bg_color: colors.Color, styles: dict) -> Table:
    """
    Render a single claim as a two-column table row:
      Left: claim text + verdict badge
      Right: stated value, filing match, delta, explanation
    """
    verdict = claim["verdict"]
    metric = claim.get("metric", "")
    claim_text = claim.get("claim_text", "")
    stated = claim.get("stated_value", "N/A")
    filing_match = claim.get("filing_match") or "—"
    delta = claim.get("filing_delta") or "—"
    explanation = claim.get("explanation", "")
    confidence = claim.get("confidence", 0.0)

    left_content = Paragraph(
        f"<b>{metric}</b><br/>{claim_text}<br/>"
        f"<font color='grey' size='7'>Confidence: {confidence:.0%}</font>",
        styles["body"],
    )
    right_content = Paragraph(
        f"<b>Stated:</b> {stated}<br/>"
        f"<b>Filing:</b> {filing_match}<br/>"
        f"<b>Delta:</b> {delta}<br/>"
        f"<font size='8'>{explanation}</font>",
        styles["body"],
    )

    t = Table([[left_content, right_content]], colWidths=[3.2 * inch, 3.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, _GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_pdf_report(session_id: str, ticker: str, output_path: str) -> str:
    """
    Generate a PDF report for the given session and save to output_path.

    Args:
        session_id: EarningsLens session UUID
        ticker: stock ticker (e.g. "NVDA")
        output_path: local file path to write the PDF

    Returns:
        The resolved output_path string.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    report = generate_json_report(session_id, ticker)
    styles = _styles()
    story = _build_story(report, styles)

    # Document setup
    doc = BaseDocTemplate(
        output_path,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"EarningsLens Report — {ticker}",
        author="EarningsLens",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="normal",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame)])
    doc.build(story)

    logger.info("PDF report written to %s", output_path)
    return output_path


def _build_story(report: dict, styles: dict) -> list:
    """Assemble the reportlab flowable story."""
    story = []
    ticker = report["ticker"]
    generated_at = report["generated_at"]
    summary = report["summary"]

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    story.append(Paragraph("EarningsLens", styles["title"]))
    story.append(Paragraph(
        f"Earnings Call Analysis Report &mdash; <b>{ticker}</b>",
        styles["subtitle"],
    ))

    dt = datetime.fromisoformat(generated_at)
    story.append(Paragraph(
        f"Generated: {dt.strftime('%B %d, %Y %H:%M UTC')}",
        styles["subtitle"],
    ))
    story.append(Spacer(1, 0.15 * inch))

    # Summary stats table
    story.append(_summary_table(summary, styles))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=1, color=_NAVY))
    story.append(Spacer(1, 0.1 * inch))

    flagged_claims = [c for c in report["claims"] if c["verdict"] == "FLAGGED"]
    verified_claims = [c for c in report["claims"] if c["verdict"] == "VERIFIED"]
    unverifiable_claims = [c for c in report["claims"] if c["verdict"] == "UNVERIFIABLE"]

    # ------------------------------------------------------------------
    # Flagged Claims
    # ------------------------------------------------------------------
    story.append(_section_header_table(
        f"  Flagged Claims  ({len(flagged_claims)})", _RED
    ))
    story.append(Spacer(1, 0.05 * inch))

    if flagged_claims:
        for claim in flagged_claims:
            story.append(_claim_block(claim, _RED_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No flagged claims.", styles["body"]))
    story.append(Spacer(1, 0.1 * inch))

    # ------------------------------------------------------------------
    # Verified Claims
    # ------------------------------------------------------------------
    story.append(_section_header_table(
        f"  Verified Claims  ({len(verified_claims)})", _GREEN
    ))
    story.append(Spacer(1, 0.05 * inch))

    if verified_claims:
        for claim in verified_claims:
            story.append(_claim_block(claim, _GREEN_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No verified claims.", styles["body"]))
    story.append(Spacer(1, 0.1 * inch))

    # ------------------------------------------------------------------
    # Unverifiable / Forward Guidance
    # ------------------------------------------------------------------
    story.append(_section_header_table(
        f"  Unverifiable / Forward Guidance  ({len(unverifiable_claims)})", _GREY
    ))
    story.append(Spacer(1, 0.05 * inch))

    if unverifiable_claims:
        for claim in unverifiable_claims:
            story.append(_claim_block(claim, _GREY_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No unverifiable claims.", styles["body"]))

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_GREY))
    story.append(Spacer(1, 0.05 * inch))
    story.append(Paragraph(
        "Powered by Amazon Nova &nbsp;|&nbsp; MacroDash &nbsp;|&nbsp; EarningsLens",
        styles["footer"],
    ))

    return story
