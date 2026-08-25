import re


def normalize_text(text: str) -> str:
    """
    Normalize text for comparisons and grouping.
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text.strip()
