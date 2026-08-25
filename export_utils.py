import io
from docx import Document

from asset_naming import asset_filename

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
_EXPORTERS = {
    "txt": export_txt,
    "md": export_md,
    "html": export_html,
    "docx": export_docx,
}


def export_generic(format: str, content: str, asset_id: str = "asset"):
    """
    Export `content` and give it a unique, unguessable filename.

    The per-format helpers above return a fixed name, which meant every user's
    export was written to the same object in a public bucket, downloading it
    returned whichever job wrote last. The name is assigned here instead.
    """
    fmt = (format or "").lower()
    exporter = _EXPORTERS.get(fmt, export_txt)

    buffer, default_name = exporter(content)
    extension = default_name.rsplit(".", 1)[-1]

    return buffer, asset_filename(asset_id, extension)
