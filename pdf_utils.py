from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, PageBreak, Flowable, Image
)
from reportlab.lib import colors
from reportlab.lib.styles import StyleSheet1, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas
import io, re, os

from asset_naming import asset_filename

PAGE_BG        = colors.HexColor("#FFFFFF")
ACCENT         = colors.HexColor("#6366F1")
ACCENT_SOFT    = colors.HexColor("#EEF2FF")
TEXT_MAIN      = colors.HexColor("#0F172A")
TEXT_MUTED     = colors.HexColor("#475569")
BORDER         = colors.HexColor("#E2E8F0")
CARD_BG        = colors.HexColor("#F8FAFC")
CODE_BG        = colors.HexColor("#F1F5F9")

def _draw_page_frame(c: Canvas, title: str):
    w, h = c._pagesize
    c.saveState()

    c.setFillColor(colors.white)
    c.rect(0, h - 60, w, 60, fill=1, stroke=0)

    c.setStrokeColor(BORDER)
    c.setLineWidth(0.6)
    c.line(40, h - 60, w - 40, h - 60)

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(TEXT_MAIN)
    c.drawString(48, h - 45, "Aetheron X402: Automated Intelligence Report")

    c.saveState()
    c.setFont("Helvetica-Bold", 55)
    c.setFillColor(colors.HexColor("#F3F4F6"))
    c.translate(w/2, h/2)
    c.rotate(22)
    c.drawCentredString(0, -60, "AETHERON X402")
    c.restoreState()

    c.restoreState()

def _footer(c: Canvas, doc):
    w, h = c._pagesize
    c.saveState()

    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(52, 40, w - 48, 40)

    c.setFont("Helvetica", 7)
    c.setFillColor(TEXT_MUTED)
    c.drawString(52, 28, "Aetheron X402: Automated Intelligence Report")
    c.drawRightString(w - 48, 28, f"Page {doc.page}")

    c.restoreState()

def escape_reportlab(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )

class MetricCard(Flowable):
    def __init__(self, name: str, value: float, max_value=10):
        super().__init__()
        self.name = name
        self.value = value
        self.max_value = max_value
        self.width = 2.3 * inch
        self.height = 0.9 * inch

    def draw(self):
        c = self.canv

        c.setFillColor(CARD_BG)
        c.roundRect(0, 0, self.width, self.height, 6, fill=1, stroke=0)

        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(TEXT_MAIN)
        c.drawString(8, self.height - 18, self.name)

        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(ACCENT)
        c.drawString(8, self.height - 38, f"{self.value:.1f}")

        c.setFont("Helvetica", 7)
        c.setFillColor(TEXT_MUTED)
        c.drawString(8, 10, f"Out of {self.max_value}")

def add_radar_chart(values, labels, size=200):
    """
    Safe radar chart generator.
    Ensures polygon receives valid numeric points and avoids crashes
    caused by malformed metric values or odd-length point lists.
    """

    if not values or len(values) < 3:
        return None

    safe_vals = []
    for v in values:
        try:
            fv = float(v)
            if fv < 0: fv = 0
            if fv > 10: fv = 10
            safe_vals.append(fv)
        except (TypeError, ValueError):
            safe_vals.append(0)

    from reportlab.graphics.shapes import Drawing, Polygon
    from math import pi, cos, sin

    d = Drawing(size, size)
    center = size / 2
    radius = size / 2 - 20
    step = 2 * pi / len(safe_vals)

    points = []
    for i, v in enumerate(safe_vals):
        angle = step * i - pi / 2
        x = center + radius * (v / 10) * cos(angle)
        y = center + radius * (v / 10) * sin(angle)
        points.append((x, y))

    flat = []
    for (x, y) in points:
        flat.extend([x, y])

    if len(flat) % 2 != 0:
        flat = flat[:-1]

    poly = Polygon(flat, fillColor=ACCENT_SOFT, strokeColor=ACCENT)
    d.add(poly)

    return d

def build_aetheron_pdf(asset_id, timestamp, wallet, title, subtitle, md_text, chart_path=None):

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=60, leftMargin=72, topMargin=170, bottomMargin=60
    )

    doc.title = title or "Aetheron X402 Asset"

    styles = StyleSheet1()
    styles.add(ParagraphStyle(name="CoverKicker", fontName="Helvetica-Bold", fontSize=9, textColor=ACCENT))
    styles.add(ParagraphStyle(name="CoverTitle", fontName="Helvetica-Bold", fontSize=22, textColor=TEXT_MAIN))
    styles.add(ParagraphStyle(name="CoverSubtitle", fontName="Helvetica", fontSize=12, textColor=TEXT_MUTED))
    styles.add(ParagraphStyle(name="Tag", fontName="Helvetica", fontSize=8, textColor=TEXT_MUTED, backColor=CARD_BG))
    styles.add(ParagraphStyle(
        name="SectionTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=20,
        textColor=TEXT_MAIN,
        spaceBefore=26,
        spaceAfter=12
    ))
    styles.add(ParagraphStyle(
        name="Body",
        fontName="Helvetica",
        fontSize=11,
        leading=17,
        spaceAfter=12,
        spaceBefore=6,
        textColor=TEXT_MAIN
    ))
    styles.add(ParagraphStyle(name="Muted", fontName="Helvetica", fontSize=8.5, textColor=TEXT_MUTED))
    styles.add(ParagraphStyle(name="CodeBlock", fontName="Courier", fontSize=9, textColor=TEXT_MAIN, backColor=CODE_BG))
    styles.add(ParagraphStyle(
        name="AetheronBullet",
        fontName="Helvetica",
        fontSize=11,
        leading=17,
        spaceAfter=6,
        leftIndent=18,
        firstLineIndent=-10,
        textColor=TEXT_MAIN
    ))

    text = md_text or ""
    text = re.sub(r"(?m)^---+$", "", text)
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"■", "", text)
    text = re.sub(r"(?s)Aetheron X402 [—-] Certified Asset.*", "", text)
    text = re.sub(r"(?s)Certified Aetheron Asset.*", "", text)

    metric_pattern = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 \-/]+):\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")
    metric_lines, cleaned = [], []
    for line in text.splitlines():
        m = metric_pattern.match(line)
        if m:
            metric_lines.append((m.group(1), float(m.group(2)), float(m.group(3))))
        else:
            cleaned.append(line)
    text = "\n".join(cleaned)

    blocks = re.split(r"\n\s*\n", text)

    def add_para(txt, style="Body"):
        if txt.strip():
            story.append(Paragraph(txt, styles[style]))

    story = []
    section_counter = 1

    story.append(Spacer(1, 0.8 * inch))

    story.append(Paragraph("AETHERON X402: INTELLIGENCE ASSET", styles["CoverKicker"]))
    story.append(Spacer(1, 0.35 * inch))

    story.append(Paragraph(title, styles["CoverTitle"]))
    story.append(Spacer(1, 0.22 * inch))

    story.append(Paragraph(subtitle, styles["CoverSubtitle"]))
    story.append(Spacer(1, 0.40 * inch))

    meta = Table([
        ["Asset ID", asset_id],
        ["Generated", timestamp],
        ["Wallet", wallet]
    ], colWidths=[1.2*inch, 3.8*inch])

    meta.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), CARD_BG),
        ("BOX", (0,0), (-1,-1), 0.7, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.4, BORDER)
    ]))

    story.append(meta)
    story.append(Spacer(1, 0.45 * inch))

    story.append(Paragraph("AI-GENERATED ANALYSIS • NOT FINANCIAL OR LEGAL ADVICE", styles["Tag"]))

    story.append(PageBreak())

    if metric_lines:
        story.append(Paragraph("Quality Metrics Overview", styles["SectionTitle"]))
        story.append(Spacer(1, 0.2*inch))

        row = []
        for label, score, max_score in metric_lines:
            row.append(MetricCard(label, score, max_score))
            if len(row) == 2:
                story.extend(row)
                story.append(Spacer(1, 0.18*inch))
                row = []
        if row:
            story.extend(row)
            story.append(Spacer(1, 0.18*inch))

        story.append(Spacer(1, 0.3*inch))

        pass

        story.append(HRFlowable(width="100%", thickness=0.8, color=BORDER))
        story.append(Spacer(1, 0.25*inch))

    story.append(Paragraph("Detailed Analysis", styles["SectionTitle"]))

    for raw in blocks:
        block = raw.strip()
        if not block:
            continue

        if block.startswith("#") or re.match(r"^\s*\d+[\.\-\)]\s+", block):
            block = re.sub(r"^[#\s]+", "", block).strip()
            
            block = re.sub(r"^\s*\d+[\.\-\)]\s*", "", block)
            section_title = f"{section_counter}. {block}"
            section_counter += 1

            story.append(Spacer(1, 0.25 * inch))
            story.append(Paragraph("▼ Section", styles["Muted"]))
            story.append(Spacer(1, 0.12 * inch))

            story.append(Paragraph(section_title, styles["SectionTitle"]))
            story.append(Spacer(1, 0.12 * inch))
            continue

        if block.startswith("```"):
            code = block.strip("`").strip()
            safe = escape_reportlab(code)
            add_para(safe.replace("\n", "<br/>"), "CodeBlock")
            continue

        if "|" in block and "---" in block:
            lines = [r.strip() for r in block.splitlines() if "|" in r]
            if len(lines) >= 2:
                header = lines[0].replace("|", " ").strip()
                add_para(f"<b>{header}</b>", "Body")
                for ln in lines[2:]:
                    cols = [c.strip() for c in ln.split("|") if c.strip()]
                    add_para(" • ".join(cols), "Body")
            continue

        lines = block.splitlines()
        if all(l.strip().startswith(("-", "*", "•")) for l in lines if l.strip()):
    
            story.append(Spacer(1, 0.15 * inch))

            for l in lines:
                clean = l.lstrip("-*• ").strip()
                story.append(Paragraph(f"• {clean}", styles["AetheronBullet"]))

            story.append(Spacer(1, 0.10 * inch))
            continue

        block = re.sub(r"^\s*\d+[\.\-\)]\s+", "", block)

        add_para(block.replace("\n", "<br/>"), "Body")

    story.append(Spacer(1, 0.4*inch))

    if chart_path and os.path.exists(chart_path):
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Simulation Distribution", styles["SectionTitle"]))
        story.append(Spacer(1, 0.12 * inch))
        story.append(Image(chart_path, width=5.7 * inch, height=3.8 * inch))
        story.append(Spacer(1, 0.25 * inch))

    story.append(HRFlowable(width="100%", thickness=0.8, color=BORDER))
    story.append(Spacer(1, 0.2*inch))

    cert = f"""
    <b>Certified Aetheron Asset</b><br/>
    Asset ID: {asset_id}<br/>
    Generated: {timestamp}<br/>
    Wallet: {wallet}<br/>
    Verification: Aetheron X402 Protocol<br/><br/>
    <font size="9" color="#6B7280">All assets are derived from large language models and on-chain
    transaction integrity. This document is provided for informational purposes only and does not
    constitute financial, legal, or investment advice.</font>
    """

    story.append(Paragraph(cert, styles["Body"]))
    story.append(Spacer(1, 0.1*inch))
    story.append(HRFlowable(width="100%", thickness=0.4, color=BORDER))

    doc.build(
        story,
        onFirstPage=lambda c, d: (_draw_page_frame(c, d.title), _footer(c, d)),
        onLaterPages=lambda c, d: (_draw_page_frame(c, d.title), _footer(c, d))
    )

    buffer.seek(0)

    filename = asset_filename(asset_id, "pdf")
    os.makedirs("generated", exist_ok=True)
    with open(os.path.join("generated", filename), "wb") as f:
        f.write(buffer.getvalue())

    return buffer, filename
