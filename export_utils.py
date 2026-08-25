import io
from docx import Document

# TXT EXPORT
def export_txt(content: str):
    buffer = io.BytesIO()
    buffer.write(content.encode("utf-8"))
    buffer.seek(0)
    return buffer, "aetheron_output.txt"


# MD EXPORT
def export_md(content: str):
    buffer = io.BytesIO()
    buffer.write(content.encode("utf-8"))
    buffer.seek(0)
    return buffer, "aetheron_output.md"


# HTML EXPORT
def export_html(content: str):
    safe_content = content.replace("\n", "<br/>")

    html = (
        "<html>"
        "<head><meta charset='UTF-8'><title>Aetheron Export</title></head>"
        "<body style='font-family: Arial; line-height: 1.6; padding: 20px;'>"
        f"{safe_content}"
        "</body>"
        "</html>"
    )

    buffer = io.BytesIO()
    buffer.write(html.encode("utf-8"))
    buffer.seek(0)
    return buffer, "aetheron_output.html"


# DOCX EXPORT
def export_docx(content: str):
    doc = Document()
    for line in content.split("\n"):
        doc.add_paragraph(line)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer, "aetheron_output.docx"


# GENERIC SELECTOR (for Celery workers)
def export_generic(format: str, content: str):
    fmt = (format or "").lower()

    if fmt == "txt":
        return export_txt(content)
    if fmt == "md":
        return export_md(content)
    if fmt == "html":
        return export_html(content)
    if fmt == "docx":
        return export_docx(content)

    # fallback = txt
    return export_txt(content)
