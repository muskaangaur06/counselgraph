#!/usr/bin/env python
"""
Generates additional sample PDFs in data/sample_contracts/ for section 9: each
targets a specific pipeline anomaly (cross-portfolio conflicts, clause dedup,
unusual governing law, ambiguous liability language, scanned/no-text-layer OCR,
and an embedded pricing table). Built with reportlab, consistent with a plain
text-flow PDF style (no external branding, generic party names only).

Usage:
    python scripts/generate_anomaly_contracts.py
"""

from __future__ import annotations

import os

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO_ROOT, "data", "sample_contracts")

_MARGIN = 1 * inch
_LINE_HEIGHT = 14
_FONT = "Helvetica"
_FONT_SIZE = 10


def _write_paragraphs(pdf_path: str, title: str, paragraphs: list[str], extra_pages: list[list[str]] | None = None) -> None:
    """Writes a simple text-flow PDF: a title, then wrapped paragraphs, paginating
    when a page fills up. extra_pages, if given, are additional pages of raw
    paragraph lists appended after the main content (used for schedule/annexure)."""
    import textwrap

    c = canvas.Canvas(pdf_path, pagesize=LETTER)
    width, height = LETTER
    max_chars_per_line = 95

    def new_page(first: bool = False):
        if not first:
            c.showPage()
        c.setFont(_FONT, _FONT_SIZE)
        return height - _MARGIN

    y = new_page(first=True)
    c.setFont(_FONT + "-Bold", 14)
    c.drawString(_MARGIN, y, title)
    y -= _LINE_HEIGHT * 2
    c.setFont(_FONT, _FONT_SIZE)

    def flow(paras: list[str]):
        nonlocal y
        for para in paras:
            for line in textwrap.wrap(para, max_chars_per_line) or [""]:
                if y < _MARGIN:
                    y = new_page()
                c.drawString(_MARGIN, y, line)
                y -= _LINE_HEIGHT
            y -= _LINE_HEIGHT * 0.5

    flow(paragraphs)
    for page_paras in (extra_pages or []):
        y = new_page()
        flow(page_paras)

    c.save()


def make_conflict_pair() -> None:
    """Two short contracts, same counterparty, contradictory termination notice
    periods (30 days vs 90 days), to feed section 6's conflict detection."""
    _write_paragraphs(
        os.path.join(OUT_DIR, "conflict_pair_a_short_notice.pdf"),
        "MASTER SERVICES AGREEMENT",
        [
            'This Master Services Agreement (the "Agreement") is entered into as of March 1, 2026, '
            'by and between Vendor Company A Pvt. Ltd. ("Client") and Vendor Company B Pvt. Ltd. '
            '("Vendor"), collectively the "Parties."',
            "1. Term and Termination",
            "This Agreement shall commence on the Effective Date and continue for an initial term of "
            "twelve (12) months. Either Party may terminate this Agreement for convenience upon thirty "
            "(30) days' prior written notice to the other Party. Either Party may terminate immediately "
            "upon a material breach that remains uncured for fifteen (15) days following written notice "
            "of such breach.",
            "2. Confidentiality",
            "Each Party agrees to hold in confidence all non-public technical, business, and financial "
            "information disclosed by the other Party, and shall not disclose such information to any "
            "third party without prior written consent, except as required by law. This obligation shall "
            "survive termination of this Agreement for a period of five (5) years.",
            "3. Governing Law",
            "This Agreement shall be governed by and construed in accordance with the laws of India. "
            "Any dispute arising out of or in connection with this Agreement shall be resolved through "
            "arbitration seated in Mumbai, in accordance with the Arbitration and Conciliation Act, 1996.",
        ],
    )
    _write_paragraphs(
        os.path.join(OUT_DIR, "conflict_pair_b_long_notice.pdf"),
        "SUPPLY AGREEMENT",
        [
            'This Supply Agreement (the "Agreement") is entered into as of June 1, 2026, by and between '
            'Vendor Company A Pvt. Ltd. ("Client") and Vendor Company B Pvt. Ltd. ("Vendor"), '
            'collectively the "Parties."',
            "1. Term and Termination",
            "This Agreement shall commence on the Effective Date and continue for an initial term of "
            "twenty-four (24) months. Either Party may terminate this Agreement for convenience upon "
            "ninety (90) days' prior written notice to the other Party. Either Party may terminate "
            "immediately upon a material breach that remains uncured for thirty (30) days following "
            "written notice of such breach.",
            "2. Confidentiality",
            "Each Party agrees to hold in confidence all non-public technical, business, and financial "
            "information disclosed by the other Party, and shall not disclose such information to any "
            "third party without prior written consent, except as required by law. This obligation shall "
            "survive termination of this Agreement for a period of five (5) years.",
            "3. Governing Law",
            "This Agreement shall be governed by and construed in accordance with the laws of India. "
            "Any dispute arising out of or in connection with this Agreement shall be resolved through "
            "arbitration seated in Mumbai, in accordance with the Arbitration and Conciliation Act, 1996.",
        ],
    )


def make_duplicate_clause_contract() -> None:
    """Same confidentiality clause repeated near-verbatim in two sections, to test
    section 2's dedup (content_hash upsert)."""
    confidentiality_text = (
        "Each Party agrees to hold in confidence all non-public technical, business, and financial "
        "information disclosed by the other Party, and shall not disclose such information to any "
        "third party without prior written consent, except as required by law. This obligation shall "
        "survive termination of this Agreement for a period of five (5) years."
    )
    _write_paragraphs(
        os.path.join(OUT_DIR, "duplicate_clause_contract.pdf"),
        "SERVICE AGREEMENT",
        [
            'This Service Agreement (the "Agreement") is entered into as of April 10, 2026, by and '
            'between Vendor Company C Pvt. Ltd. ("Client") and Vendor Company D Pvt. Ltd. ("Vendor"), '
            'collectively the "Parties."',
            "1. Confidentiality",
            confidentiality_text,
            "2. Term and Termination",
            "This Agreement shall commence on the Effective Date and continue for an initial term of "
            "twelve (12) months. Either Party may terminate this Agreement for convenience upon sixty "
            "(60) days' prior written notice to the other Party.",
            "3. Limitation of Liability",
            "Except for breaches of the Confidentiality clause, in no event shall either Party's "
            "aggregate liability arising out of or related to this Agreement exceed the total fees "
            "paid in the twelve (12) months preceding the claim.",
            "4. Confidentiality (Restated)",
            "For the avoidance of doubt, and restating the obligation above: " + confidentiality_text,
            "5. Governing Law",
            "This Agreement shall be governed by and construed in accordance with the laws of India.",
        ],
    )


def make_unusual_governing_law_contract() -> None:
    """Governing law set to an unexpected jurisdiction relative to an org profile's
    default (IN), to exercise the "unfavorable governing law" risk flag."""
    _write_paragraphs(
        os.path.join(OUT_DIR, "unusual_governing_law_contract.pdf"),
        "OUTSOURCING AGREEMENT",
        [
            'This Outsourcing Agreement (the "Agreement") is entered into as of May 5, 2026, by and '
            'between Vendor Company E Pvt. Ltd. ("Client") and Vendor Company F Ltd. ("Vendor"), '
            'collectively the "Parties."',
            "1. Term and Termination",
            "This Agreement shall commence on the Effective Date and continue for an initial term of "
            "thirty-six (36) months. Either Party may terminate this Agreement for convenience upon "
            "sixty (60) days' prior written notice to the other Party.",
            "2. Confidentiality",
            "Each Party agrees to hold in confidence all non-public technical, business, and financial "
            "information disclosed by the other Party. This obligation shall survive termination of "
            "this Agreement for a period of five (5) years.",
            "3. Limitation of Liability",
            "Except for breaches of the Confidentiality clause, in no event shall either Party's "
            "aggregate liability arising out of or related to this Agreement exceed the total fees "
            "paid in the twelve (12) months preceding the claim.",
            "4. Indemnification",
            "Each Party shall indemnify, defend, and hold harmless the other Party from any third-party "
            "claims arising from the indemnifying Party's gross negligence or willful misconduct in the "
            "performance of its obligations under this Agreement.",
            "5. Governing Law",
            "This Agreement shall be governed by and construed in accordance with the laws of the "
            "Cayman Islands. Any dispute arising out of or in connection with this Agreement shall be "
            "resolved through arbitration seated in George Town, Cayman Islands.",
        ],
    )


def make_ambiguous_liability_contract() -> None:
    """Liability cap clause written in vague non-standard wording instead of a
    clean number, to exercise "Non-Standard Wording" / ambiguous-terms flagging."""
    _write_paragraphs(
        os.path.join(OUT_DIR, "ambiguous_liability_contract.pdf"),
        "MAINTENANCE AGREEMENT",
        [
            'This Maintenance Agreement (the "Agreement") is entered into as of July 20, 2026, by and '
            'between Vendor Company G Pvt. Ltd. ("Client") and Vendor Company H Pvt. Ltd. ("Vendor"), '
            'collectively the "Parties."',
            "1. Term and Termination",
            "This Agreement shall commence on the Effective Date and continue for an initial term of "
            "twelve (12) months. Either Party may terminate this Agreement for convenience upon sixty "
            "(60) days' prior written notice to the other Party.",
            "2. Confidentiality",
            "Each Party agrees to hold in confidence all non-public technical, business, and financial "
            "information disclosed by the other Party. This obligation shall survive termination of "
            "this Agreement for a period of five (5) years.",
            "3. Limitation of Liability",
            "The Vendor's liability under this Agreement shall be limited to a reasonable amount having "
            "regard to the circumstances then prevailing, as may be mutually agreed by the Parties in "
            "good faith from time to time, it being understood that no Party intends for this clause to "
            "impose an unreasonable or unbounded exposure on the other, without prejudice to either "
            "Party's other rights and remedies under applicable law.",
            "4. Governing Law",
            "This Agreement shall be governed by and construed in accordance with the laws of India.",
        ],
    )


def make_scanned_image_pdf() -> None:
    """Low-DPI rendered image of a page with no embedded text layer, to genuinely
    exercise OCR (detect_low_text_pages -> ocr_pages), not just text-based PDFs."""
    img_width, img_height = 1000, 1300  # roughly letter-ratio at low effective DPI
    img = Image.new("L", (img_width, img_height), color=255)
    draw = ImageDraw.Draw(img)

    lines = [
        "NON-DISCLOSURE AGREEMENT",
        "",
        "This Non-Disclosure Agreement is entered into as of August 1, 2026,",
        'by and between Vendor Company I Pvt. Ltd. ("Disclosing Party") and',
        'Vendor Company J Pvt. Ltd. ("Receiving Party").',
        "",
        "1. Confidentiality",
        "The Receiving Party shall hold all Confidential Information in strict",
        "confidence and shall not disclose it to any third party for a period",
        "of three (3) years from the date of disclosure.",
        "",
        "2. Governing Law",
        "This Agreement shall be governed by the laws of India, with courts",
        "in Bengaluru having exclusive jurisdiction.",
        "",
        "3. Term",
        "This Agreement shall remain in effect for two (2) years from the",
        "Effective Date unless earlier terminated by either Party upon",
        "thirty (30) days' written notice.",
    ]
    y = 60
    for line in lines:
        draw.text((60, y), line, fill=0)
        y += 55

    img_path = os.path.join(OUT_DIR, "_scanned_page_source.png")
    img.save(img_path, dpi=(100, 100))  # low DPI on purpose: genuinely poor scan quality

    pdf_path = os.path.join(OUT_DIR, "poor_quality_scanned_nda.pdf")
    c = canvas.Canvas(pdf_path, pagesize=LETTER)
    width, height = LETTER
    c.drawImage(img_path, 0, 0, width=width, height=height)
    c.save()
    os.remove(img_path)


def make_pricing_table_contract() -> None:
    """A longer contract with an embedded pricing table across a schedule/annexure,
    to exercise pdfplumber's table extraction (build_table_records/build_table_chunks)."""
    from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    pdf_path = os.path.join(OUT_DIR, "long_contract_with_pricing_schedule.pdf")
    doc = SimpleDocTemplate(pdf_path, pagesize=LETTER, topMargin=_MARGIN, bottomMargin=_MARGIN,
                             leftMargin=_MARGIN, rightMargin=_MARGIN)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("MASTER SUPPLY AGREEMENT", styles["Title"]))
    story.append(Spacer(1, 12))

    body_paragraphs = [
        'This Master Supply Agreement (the "Agreement") is entered into as of September 1, 2026, by '
        'and between Vendor Company K Pvt. Ltd. ("Client") and Vendor Company L Pvt. Ltd. ("Vendor"), '
        'collectively the "Parties."',
        "1. Term and Termination. This Agreement shall commence on the Effective Date and continue "
        "for an initial term of thirty-six (36) months. Either Party may terminate this Agreement for "
        "convenience upon ninety (90) days' prior written notice to the other Party.",
        "2. Confidentiality. Each Party agrees to hold in confidence all non-public technical, "
        "business, and financial information disclosed by the other Party. This obligation shall "
        "survive termination of this Agreement for a period of five (5) years.",
        "3. Limitation of Liability. Except for breaches of the Confidentiality clause, in no event "
        "shall either Party's aggregate liability arising out of or related to this Agreement exceed "
        "the total fees paid in the twelve (12) months preceding the claim.",
        "4. Indemnification. Each Party shall indemnify, defend, and hold harmless the other Party "
        "from any third-party claims arising from the indemnifying Party's gross negligence or "
        "willful misconduct in the performance of its obligations under this Agreement.",
        "5. Governing Law. This Agreement shall be governed by and construed in accordance with the "
        "laws of India, with disputes resolved through arbitration seated in Mumbai.",
        "6. Pricing. Pricing for goods supplied under this Agreement is set out in Schedule A below, "
        "and is subject to the annual review mechanism described in Section 7.",
        "7. Annual Review. The prices in Schedule A shall be reviewed annually and may be adjusted by "
        "mutual written agreement of the Parties, provided that no single annual adjustment shall "
        "exceed ten percent (10%) of the then-current price for any line item.",
    ]
    for para in body_paragraphs:
        story.append(Paragraph(para, styles["BodyText"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph("SCHEDULE A: PRICING", styles["Heading2"]))
    story.append(Spacer(1, 8))

    table_data = [
        ["Item Code", "Description", "Unit Price (INR)", "Minimum Order Qty", "Lead Time (days)"],
        ["SKU-1001", "Component A - Standard Grade", "1,250.00", "500", "21"],
        ["SKU-1002", "Component A - Premium Grade", "1,890.00", "250", "28"],
        ["SKU-1003", "Component B - Standard Grade", "740.00", "1000", "14"],
        ["SKU-1004", "Component B - Premium Grade", "1,120.00", "500", "21"],
        ["SKU-2001", "Sub-Assembly Kit - Type I", "6,450.00", "100", "35"],
        ["SKU-2002", "Sub-Assembly Kit - Type II", "8,975.00", "50", "42"],
        ["SKU-3001", "Replacement Part Set - Basic", "395.00", "2000", "10"],
        ["SKU-3002", "Replacement Part Set - Extended", "610.00", "1000", "14"],
    ]
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "8. Audit Rights. Client shall have the right, upon reasonable prior notice and no more than "
        "once per twelve (12) month period, to audit Vendor's compliance with its security, data "
        "protection, and confidentiality obligations under this Agreement.",
        styles["BodyText"],
    ))

    doc.build(story)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    make_conflict_pair()
    make_duplicate_clause_contract()
    make_unusual_governing_law_contract()
    make_ambiguous_liability_contract()
    make_scanned_image_pdf()
    make_pricing_table_contract()
    print("Generated anomaly sample contracts in", OUT_DIR)


if __name__ == "__main__":
    main()
