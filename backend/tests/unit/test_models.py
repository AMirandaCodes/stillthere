"""
Unit tests for ORM model logic that doesn't require a database connection.
"""
from app.models.contact import Contact
from app.models.company import Company
from app.models.batch_job import BatchJob
from app.models.enums import BatchJobStatus


class TestContactModel:
    def test_normalized_name_set_on_full_name_assign(self):
        c = Contact(full_name="John Smith", email=None)
        c.full_name = "John Smith"
        # The @validates decorator fires on assignment
        # SQLAlchemy calls it during __init__ too
        assert c.normalized_name == "john smith"

    def test_normalized_name_collapses_whitespace(self):
        c = Contact(full_name="  Jane   Doe  ", email=None)
        c.full_name = "  Jane   Doe  "
        assert c.normalized_name == "jane doe"

    def test_normalized_name_lowercased(self):
        c = Contact(full_name="ALICE BOBSON", email=None)
        c.full_name = "ALICE BOBSON"
        assert c.normalized_name == "alice bobson"


class TestCompanyModel:
    def test_normalized_name_lowercased(self):
        co = Company(name="Acme Ltd", domain=None, website=None)
        co.name = "Acme Ltd"
        assert co.normalized_name == "acme ltd"


class TestBatchJobModel:
    def test_progress_percentage_zero_when_no_records(self):
        job = BatchJob(filename="test.csv", status=BatchJobStatus.QUEUED, total_records=0)
        job.processed_records = 0
        assert job.progress_percentage == 0

    def test_progress_percentage_correct(self):
        job = BatchJob(filename="test.csv", status=BatchJobStatus.RUNNING, total_records=100)
        job.processed_records = 50
        assert job.progress_percentage == 50

    def test_progress_percentage_rounds_down(self):
        job = BatchJob(filename="test.csv", status=BatchJobStatus.RUNNING, total_records=3)
        job.processed_records = 1
        assert job.progress_percentage == 33
