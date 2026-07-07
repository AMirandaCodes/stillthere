"""
CSV parsing utilities for batch upload.

Extracted from BatchService (SP-01) so CSV format changes have a single module
to change without touching the orchestration layer.
"""
import csv
import io

_REQUIRED_COLS = frozenset({"name", "company"})


class BatchValidationError(ValueError):
    """Raised when the uploaded CSV fails structural validation."""


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """
    Parse CSV text into (normalised_headers, rows).

    Headers are lowercased and stripped.  Rows with all-empty values are excluded.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    raw_fields = reader.fieldnames or []
    headers = [h.strip().lower() for h in raw_fields if h is not None]
    rows: list[dict[str, str]] = []
    for row in reader:
        normalised = {
            k.strip().lower(): (v or "").strip()
            for k, v in row.items()
            if k is not None
        }
        if any(normalised.values()):
            rows.append(normalised)
    return headers, rows


def validate_columns(headers: list[str]) -> None:
    """Raise BatchValidationError if required columns are absent."""
    missing = _REQUIRED_COLS - set(headers)
    if missing:
        raise BatchValidationError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            "Expected headers: Name, Company (case-insensitive). Email is optional."
        )


def clean(value: str) -> str:
    return value.strip()
