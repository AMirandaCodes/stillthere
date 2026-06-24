"""
Unit tests for Pydantic schemas — input validation and sanitisation.
"""
import pytest
from pydantic import ValidationError

from app.schemas.verification import VerificationCreate
from app.models.enums import TriState, ConfidenceLevel, VerificationStatus


class TestVerificationCreate:
    def test_valid_minimal(self):
        payload = VerificationCreate(full_name="John Smith", company_name="Acme Ltd")
        assert payload.full_name == "John Smith"
        assert payload.company_name == "Acme Ltd"
        assert payload.work_email is None

    def test_valid_with_email(self):
        payload = VerificationCreate(
            full_name="Jane Doe",
            company_name="XYZ Corp",
            work_email="jane.doe@xyz.com",
        )
        assert payload.work_email == "jane.doe@xyz.com"

    def test_email_normalised_to_lowercase(self):
        payload = VerificationCreate(
            full_name="John Smith",
            company_name="Acme Ltd",
            work_email="  John.Smith@ACME.COM  ",
        )
        assert payload.work_email == "john.smith@acme.com"

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            VerificationCreate(full_name="", company_name="Acme Ltd")

    def test_empty_company_raises(self):
        with pytest.raises(ValidationError):
            VerificationCreate(full_name="John Smith", company_name="")

    def test_html_stripped_from_name(self):
        payload = VerificationCreate(
            full_name="<script>alert('xss')</script>John",
            company_name="Acme Ltd",
        )
        assert "<script>" not in payload.full_name
        assert "John" in payload.full_name

    def test_html_stripped_from_company(self):
        payload = VerificationCreate(
            full_name="John Smith",
            company_name="<b>Acme</b> Ltd",
        )
        assert "<b>" not in payload.company_name


class TestEnums:
    def test_tristate_values(self):
        assert set(TriState) == {TriState.YES, TriState.NO, TriState.UNCLEAR}

    def test_confidence_level_values(self):
        assert set(ConfidenceLevel) == {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW}

    def test_verification_status_values(self):
        assert VerificationStatus.PENDING == "pending"
        assert VerificationStatus.COMPLETE == "complete"

    def test_tristate_str_equality(self):
        # Ensures enums work as string values in JSON serialisation
        assert TriState.UNCLEAR == "unclear"
        assert TriState.YES == "yes"
