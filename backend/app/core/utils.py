import re


def normalize_name(value: str) -> str:
    """Collapse whitespace and lowercase a name string for deduplication."""
    return re.sub(r"\s+", " ", value.strip().lower())


def format_exc_message(exc: Exception, max_len: int = 500) -> str:
    """Format an exception as a short string safe to store in the database."""
    return f"{type(exc).__name__}: {exc}"[:max_len]
