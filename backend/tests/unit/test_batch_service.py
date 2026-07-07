"""
Unit tests for CSV parsing utilities (app.services.csv_parser).

These functions have no DB dependencies — call them directly.
The DB-interaction methods (upload, get_job, etc.) are covered in the
integration tests (test_batch_pipeline.py, test_batch_endpoints.py).

Note: functions were extracted from BatchService into csv_parser (SP-01).
"""
import pytest

from app.services.csv_parser import (
    BatchValidationError,
    clean,
    parse_csv,
    validate_columns,
)


# ── parse_csv ─────────────────────────────────────────────────────────────────

class TestParseCsv:
    def test_basic_rows_returned(self):
        csv = "Name,Company,Email\nAlice,Acme,alice@acme.com\nBob,Globex,"
        headers, rows = parse_csv(csv)
        assert headers == ["name", "company", "email"]
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
        assert rows[0]["company"] == "Acme"
        assert rows[0]["email"] == "alice@acme.com"

    def test_headers_normalised_to_lowercase(self):
        csv = "NAME,COMPANY\nAlice,Acme"
        headers, _ = parse_csv(csv)
        assert headers == ["name", "company"]

    def test_headers_with_spaces_stripped(self):
        csv = " Name , Company \nAlice,Acme"
        headers, _ = parse_csv(csv)
        assert "name" in headers
        assert "company" in headers

    def test_empty_all_blank_rows_excluded(self):
        csv = "Name,Company\nAlice,Acme\n,\n   ,   "
        _, rows = parse_csv(csv)
        assert len(rows) == 1

    def test_bom_prefix_handled(self):
        # UTF-8 BOM (﻿) is stripped by decode("utf-8-sig") before parse_csv
        csv = "﻿Name,Company\nAlice,Acme"
        headers, rows = parse_csv(csv)
        assert "name" in headers
        assert len(rows) == 1

    def test_extra_columns_passed_through(self):
        csv = "Name,Company,Email,Notes\nAlice,Acme,a@a.com,VIP"
        headers, rows = parse_csv(csv)
        assert "notes" in headers
        assert rows[0]["notes"] == "VIP"

    def test_empty_csv_returns_no_rows(self):
        csv = "Name,Company\n"
        _, rows = parse_csv(csv)
        assert rows == []

    def test_values_stripped_of_whitespace(self):
        csv = "Name,Company\n  Alice  ,  Acme  "
        _, rows = parse_csv(csv)
        assert rows[0]["name"] == "Alice"
        assert rows[0]["company"] == "Acme"


# ── validate_columns ──────────────────────────────────────────────────────────

class TestValidateColumns:
    def test_valid_headers_no_error(self):
        validate_columns(["name", "company"])  # should not raise

    def test_valid_headers_with_email_no_error(self):
        validate_columns(["name", "company", "email"])

    def test_valid_headers_with_extra_cols(self):
        validate_columns(["name", "company", "phone", "notes"])

    def test_missing_name_raises(self):
        with pytest.raises(BatchValidationError, match="name"):
            validate_columns(["company", "email"])

    def test_missing_company_raises(self):
        with pytest.raises(BatchValidationError, match="company"):
            validate_columns(["name", "email"])

    def test_missing_both_raises(self):
        with pytest.raises(BatchValidationError):
            validate_columns(["email", "phone"])

    def test_empty_headers_raises(self):
        with pytest.raises(BatchValidationError):
            validate_columns([])

    def test_case_sensitive_after_normalisation(self):
        # validate_columns expects already-normalised (lowercase) headers
        # Mixed case should fail because parse_csv lowercases them first
        with pytest.raises(BatchValidationError):
            validate_columns(["Name", "Company"])  # not normalised


# ── clean ─────────────────────────────────────────────────────────────────────

class TestClean:
    def test_strips_whitespace(self):
        assert clean("  hello  ") == "hello"

    def test_empty_string_unchanged(self):
        assert clean("") == ""

    def test_no_whitespace_unchanged(self):
        assert clean("Alice") == "Alice"

    def test_internal_whitespace_preserved(self):
        assert clean("  Alice Smith  ") == "Alice Smith"


# ── Row-level validation via parse_csv + validate_columns ────────────────────

class TestCsvRowValidation:
    """End-to-end CSV parsing + column validation as BatchService.upload does it."""

    def _process(self, csv_text: str):
        headers, rows = parse_csv(csv_text)
        validate_columns(headers)
        valid, skipped = [], []
        for row in rows:
            if clean(row.get("name", "")) and clean(row.get("company", "")):
                valid.append(row)
            else:
                skipped.append(row)
        return valid, skipped

    def test_all_valid_rows(self):
        csv = "Name,Company\nAlice,Acme\nBob,Globex"
        valid, skipped = self._process(csv)
        assert len(valid) == 2
        assert skipped == []

    def test_empty_name_row_skipped(self):
        csv = "Name,Company\n,Acme\nBob,Globex"
        valid, skipped = self._process(csv)
        assert len(valid) == 1
        assert len(skipped) == 1

    def test_empty_company_row_skipped(self):
        csv = "Name,Company\nAlice,\nBob,Globex"
        valid, skipped = self._process(csv)
        assert len(valid) == 1
        assert len(skipped) == 1

    def test_whitespace_only_treated_as_empty(self):
        csv = "Name,Company\n   , Acme\nBob,Globex"
        valid, skipped = self._process(csv)
        assert len(valid) == 1
        assert len(skipped) == 1

    def test_all_rows_skipped_if_no_valid_data(self):
        csv = "Name,Company\n,\n ,"
        _, rows = parse_csv(csv)
        # All-blank rows are excluded by parse_csv itself
        assert rows == []
