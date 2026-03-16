"""
pdf_exporter.py: chart-forward PDF report generation for EarningsLens.

Produces a demo-friendly PDF with:
  - Executive summary and verdict stats
  - Colorful verdict and market context charts
  - MacroDash snapshot cards and headlines
  - Detailed claim review blocks
"""

import logging
from datetime import datetime
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from backend.report.json_exporter import generate_json_report

logger = logging.getLogger(__name__)

_NAVY = colors.HexColor("#0F172A")
_SLATE = colors.HexColor("#475569")
_PANEL = colors.HexColor("#F8FAFC")
_ORANGE = colors.HexColor("#F97316")
_AMBER = colors.HexColor("#F59E0B")
_GREEN = colors.HexColor("#10B981")
_GREEN_BG = colors.HexColor("#ECFDF5")
_RED = colors.HexColor("#F43F5E")
_RED_BG = colors.HexColor("#FFF1F2")
_BLUE = colors.HexColor("#0EA5E9")
_BLUE_BG = colors.HexColor("#F0F9FF")
_VIOLET = colors.HexColor("#8B5CF6")
_GREY = colors.HexColor("#94A3B8")
_GREY_BG = colors.HexColor("#F8FAFC")
_BORDER = colors.HexColor("#CBD5E1")
_WHITE = colors.white


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ELTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=26,
            textColor=_NAVY,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ELSubtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=_SLATE,
            alignment=TA_CENTER,
            spaceAfter=3,
        ),
        "section_header": ParagraphStyle(
            "ELSectionHeader",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
            textColor=_WHITE,
        ),
        "card_label": ParagraphStyle(
            "ELCardLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=_SLATE,
        ),
        "card_value": ParagraphStyle(
            "ELCardValue",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=16,
            textColor=_NAVY,
        ),
        "body": ParagraphStyle(
            "ELBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=_NAVY,
            spaceAfter=2,
        ),
        "body_small": ParagraphStyle(
            "ELBodySmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=_SLATE,
            spaceAfter=1,
        ),
        "footer": ParagraphStyle(
            "ELFooter",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=10,
            textColor=_SLATE,
            alignment=TA_CENTER,
        ),
    }


def _fmt_number(value: float | None, prefix: str = "", suffix: str = "", decimals: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{prefix}{value:,.{decimals}f}{suffix}"


def _fmt_compact(value: float | None) -> str:
    if value is None:
        return "N/A"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.1f}T"
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def _section_header_table(label: str, bg_color: colors.Color) -> Table:
    table = Table([[label]], colWidths=[7 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("TEXTCOLOR", (0, 0), (-1, -1), _WHITE),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _summary_table(summary: dict, styles: dict) -> Table:
    cells = [
        ("Total Claims", str(summary["total_claims"]), _BLUE_BG),
        ("Verified", str(summary["verified"]), _GREEN_BG),
        ("Flagged", str(summary["flagged"]), _RED_BG),
        ("Unverifiable", str(summary["unverifiable"]), _GREY_BG),
        ("Verify Rate", f"{summary['verification_rate'] * 100:.1f}%", colors.HexColor("#FFF7ED")),
    ]
    row = [
        Paragraph(
            f"<font size='8' color='{_SLATE.hexval()}'>{label}</font><br/><font size='15'><b>{value}</b></font>",
            styles["body"],
        )
        for label, value, _ in cells
    ]
    table = Table([row], colWidths=[1.36 * inch] * 5)
    style = [
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, _BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    for idx, (_, _, bg) in enumerate(cells):
        style.append(("BACKGROUND", (idx, 0), (idx, 0), bg))
    table.setStyle(TableStyle(style))
    return table


def _bar_chart(
    title: str,
    items: list[tuple[str, float, colors.Color]],
    width: int = 300,
    height: int = 150,
) -> Drawing:
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 10, title, fontName="Helvetica-Bold", fontSize=10, fillColor=_NAVY))

    max_value = max((value for _, value, _ in items), default=1) or 1
    chart_top = height - 35
    bar_bottom = 28
    usable_height = max(chart_top - bar_bottom, 40)
    bar_width = 44
    gap = 20
    start_x = 24

    drawing.add(Line(16, bar_bottom, width - 10, bar_bottom, strokeColor=_BORDER, strokeWidth=1))
    for idx, (label, value, color) in enumerate(items):
        bar_height = (value / max_value) * usable_height if max_value else 0
        x = start_x + idx * (bar_width + gap)
        drawing.add(Rect(x, bar_bottom, bar_width, bar_height, fillColor=color, strokeColor=color, rx=4, ry=4))
        drawing.add(String(x + bar_width / 2, bar_bottom + bar_height + 6, f"{value:.1f}" if value % 1 else f"{int(value)}",
                           textAnchor="middle", fontName="Helvetica-Bold", fontSize=8, fillColor=_NAVY))
        drawing.add(String(x + bar_width / 2, 12, label, textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=_SLATE))
    return drawing


def _market_cards(snapshot: dict, styles: dict) -> Table:
    cards = [
        ("Price", _fmt_number(snapshot.get("price"), prefix="$", decimals=2), _BLUE_BG),
        ("Change", _fmt_number(snapshot.get("change_pct"), suffix="%", decimals=2), _GREEN_BG if (snapshot.get("change_pct") or 0) >= 0 else _RED_BG),
        ("RSI", _fmt_number(snapshot.get("rsi"), decimals=1), _GREY_BG),
        ("MACD", _fmt_number(snapshot.get("macd"), decimals=2), _GREY_BG),
        ("GDP", _fmt_number(snapshot.get("gdp_growth"), suffix="%", decimals=1), _GREY_BG),
        ("PCE", _fmt_number(snapshot.get("pce"), suffix="%", decimals=1), _GREY_BG),
        ("Inflation", _fmt_number(snapshot.get("inflation"), suffix="%", decimals=1), _GREY_BG),
        ("Unemployment", _fmt_number(snapshot.get("unemployment_rate"), suffix="%", decimals=1), _GREY_BG),
        ("Market Cap", _fmt_compact(snapshot.get("market_cap")), _GREY_BG),
        ("P/E", _fmt_number(snapshot.get("pe_ratio"), decimals=1), _GREY_BG),
    ]
    rows = []
    row = []
    for idx, (label, value, bg) in enumerate(cards, start=1):
        row.append(Paragraph(f"<font size='7' color='{_SLATE.hexval()}'>{label}</font><br/><font size='12'><b>{value}</b></font>", styles["body"]))
        if idx % 5 == 0:
            rows.append(row)
            row = []
    if row:
        while len(row) < 5:
            row.append(Paragraph("", styles["body"]))
        rows.append(row)

    table = Table(rows, colWidths=[1.36 * inch] * 5)
    style = [
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    for idx, (_, _, bg) in enumerate(cards):
        row_idx = idx // 5
        col_idx = idx % 5
        style.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), bg))
    table.setStyle(TableStyle(style))
    return table


def _claim_block(claim: dict, bg_color: colors.Color, styles: dict) -> Table:
    metric = claim.get("metric") or "Claim"
    claim_text = claim.get("claim_text", "")
    stated = claim.get("stated_value", "N/A")
    filing_match = claim.get("filing_match") or "—"
    delta = claim.get("filing_delta") or "—"
    confidence = claim.get("confidence", 0.0)
    technical_context = claim.get("technical_context", "") or "No technical context captured."
    macro_context = claim.get("macro_context", "") or "No macro context captured."
    explanation = claim.get("explanation", "") or "No explanation available."

    left = Paragraph(
        f"<b>{metric}</b><br/>{claim_text}<br/><font size='7' color='{_SLATE.hexval()}'>Confidence: {confidence:.0%}</font>",
        styles["body"],
    )
    right = Paragraph(
        f"<b>Stated:</b> {stated}<br/>"
        f"<b>Filing:</b> {filing_match}<br/>"
        f"<b>Delta:</b> {delta}<br/>"
        f"<b>Tech:</b> {technical_context}<br/>"
        f"<b>Macro:</b> {macro_context}<br/>"
        f"<font size='8'>{explanation}</font>",
        styles["body"],
    )
    table = Table([[left, right]], colWidths=[2.6 * inch, 4.4 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def _headline_list(headlines: list[str], styles: dict) -> list:
    if not headlines:
        return [Paragraph("No recent MacroDash headlines were cached for this session.", styles["body"])]
    return [
        Paragraph(f"<b>{idx}.</b> {headline}", styles["body"])
        for idx, headline in enumerate(headlines, start=1)
    ]


def generate_pdf_report(session_id: str, ticker: str, output_path: str) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    report = generate_json_report(session_id, ticker)
    styles = _styles()
    story = _build_story(report, styles)

    doc = BaseDocTemplate(
        output_path,
        pagesize=LETTER,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.72 * inch,
        bottomMargin=0.72 * inch,
        title=f"EarningsLens Report — {ticker}",
        author="EarningsLens",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame)])
    doc.build(story)

    logger.info("PDF report written to %s", output_path)
    return output_path


def _build_story(report: dict, styles: dict) -> list:
    summary = report["summary"]
    snapshot = report.get("market_context", {}).get("snapshot", {})
    headlines = report.get("market_context", {}).get("news_headlines", [])
    flagged_claims = [c for c in report["claims"] if c["verdict"] == "FLAGGED"]
    verified_claims = [c for c in report["claims"] if c["verdict"] == "VERIFIED"]
    unverifiable_claims = [c for c in report["claims"] if c["verdict"] == "UNVERIFIABLE"]

    dt = datetime.fromisoformat(report["generated_at"])
    verdict_chart = _bar_chart(
        "Verification Mix",
        [
            ("Verified", summary["verified"], _GREEN),
            ("Flagged", summary["flagged"], _RED),
            ("Unverifiable", summary["unverifiable"], _GREY),
        ],
    )
    market_chart = _bar_chart(
        "MacroDash Snapshot",
        [
            ("RSI", snapshot.get("rsi") or 0, _ORANGE),
            ("GDP", snapshot.get("gdp_growth") or 0, _BLUE),
            ("PCE", snapshot.get("pce") or 0, _VIOLET),
            ("Infl.", snapshot.get("inflation") or 0, _AMBER),
            ("Unemp.", snapshot.get("unemployment_rate") or 0, _RED),
        ],
    )

    story: list = []
    story.append(Paragraph("EarningsLens", styles["title"]))
    story.append(Paragraph(
        f"Earnings Call Analysis Report &mdash; <b>{report['ticker']}</b>",
        styles["subtitle"],
    ))
    story.append(Paragraph(
        f"Generated {dt.strftime('%B %d, %Y %H:%M UTC')} &nbsp;|&nbsp; Powered by Amazon Nova + MacroDash",
        styles["subtitle"],
    ))
    story.append(Spacer(1, 0.14 * inch))
    story.append(_summary_table(summary, styles))
    story.append(Spacer(1, 0.16 * inch))

    charts = Table([[verdict_chart, market_chart]], colWidths=[3.45 * inch, 3.45 * inch])
    charts.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(charts)
    story.append(Spacer(1, 0.16 * inch))

    story.append(_section_header_table("  MacroDash Market Snapshot", _NAVY))
    story.append(Spacer(1, 0.06 * inch))
    story.append(_market_cards(snapshot, styles))
    story.append(Spacer(1, 0.12 * inch))

    story.append(_section_header_table("  MacroDash Headlines", _ORANGE))
    story.append(Spacer(1, 0.06 * inch))
    for item in _headline_list(headlines, styles):
        story.append(item)
    story.append(Spacer(1, 0.16 * inch))
    story.append(HRFlowable(width="100%", thickness=0.75, color=_BORDER))
    story.append(PageBreak())

    story.append(_section_header_table(f"  Flagged Claims  ({len(flagged_claims)})", _RED))
    story.append(Spacer(1, 0.06 * inch))
    if flagged_claims:
        for claim in flagged_claims:
            story.append(_claim_block(claim, _RED_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No flagged claims.", styles["body"]))
    story.append(Spacer(1, 0.12 * inch))

    story.append(_section_header_table(f"  Verified Claims  ({len(verified_claims)})", _GREEN))
    story.append(Spacer(1, 0.06 * inch))
    if verified_claims:
        for claim in verified_claims:
            story.append(_claim_block(claim, _GREEN_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No verified claims.", styles["body"]))
    story.append(Spacer(1, 0.12 * inch))

    story.append(_section_header_table(f"  Unverifiable / Forward Guidance  ({len(unverifiable_claims)})", _GREY))
    story.append(Spacer(1, 0.06 * inch))
    if unverifiable_claims:
        for claim in unverifiable_claims:
            story.append(_claim_block(claim, _GREY_BG, styles))
            story.append(Spacer(1, 0.05 * inch))
    else:
        story.append(Paragraph("No unverifiable claims.", styles["body"]))

    story.append(Spacer(1, 0.18 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        "Structured claim verification, SEC grounding, and live MacroDash context exported for demo review.",
        styles["footer"],
    ))
    return story
