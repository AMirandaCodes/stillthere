"""
Input sanitisation and security utilities.
Keeps route handlers clean — all untrusted input passes through here first.
"""
import re
import html


_ALLOWED_NAME_RE = re.compile(r"[^\w\s\-\.\'\,]", re.UNICODE)
_ALLOWED_COMPANY_RE = re.compile(r"[^\w\s\-\.\'\,\&\(\)]", re.UNICODE)
_MAX_FIELD_LENGTH = 500


def sanitise_name(value: str) -> str:
    value = html.escape(value.strip())
    value = _ALLOWED_NAME_RE.sub("", value)
    return value[:_MAX_FIELD_LENGTH]


def sanitise_company(value: str) -> str:
    value = html.escape(value.strip())
    value = _ALLOWED_COMPANY_RE.sub("", value)
    return value[:_MAX_FIELD_LENGTH]


def sanitise_email(value: str) -> str:
    return value.strip().lower()[:_MAX_FIELD_LENGTH]
