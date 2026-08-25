import logging
import os

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

# Generated artefacts are served straight from the R2 public base, so the
# Content-Type has to be right or browsers download .docx files as plain text.
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".zip": "application/zip",
}


def _content_type_for(filename: str) -> str:
    _, _, ext = filename.lower().rpartition(".")
    return _CONTENT_TYPES.get(f".{ext}", "application/octet-stream")


def get_r2_client():
    """Create an S3-compatible client for Cloudflare R2."""
    endpoint = os.getenv("R2_ENDPOINT")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")

    missing = [
        name
        for name, value in (
            ("R2_ENDPOINT", endpoint),
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"R2 is not configured — missing {', '.join(missing)}. "
            f"See .env.example, or set them in the Railway variables."
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def r2_upload_bytes(data: bytes, filename: str) -> str:
    """Upload bytes to Cloudflare R2 and return a public download URL."""
    bucket = os.getenv("R2_BUCKET_NAME")
    public_base = (os.getenv("R2_PUBLIC_BASE") or "").rstrip("/")

    missing = [
        name
        for name, value in (
            ("R2_BUCKET_NAME", bucket),
            ("R2_PUBLIC_BASE", public_base),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"R2 is not configured — missing {', '.join(missing)}. "
            f"See .env.example, or set them in the Railway variables."
        )

    get_r2_client().put_object(
        Bucket=bucket,
        Key=filename,
        Body=data,
        ContentType=_content_type_for(filename),
        ContentDisposition=f'attachment; filename="{filename}"',
    )

    logger.info("Uploaded %s to R2 (%d bytes)", filename, len(data))
    return f"{public_base}/{filename}"
