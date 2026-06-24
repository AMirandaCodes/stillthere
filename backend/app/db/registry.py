"""
Import all ORM models so SQLAlchemy and Alembic can discover them.
Import this module (not app.db.base) whenever you need the full metadata.
Order: no-FK models first, deepest FK dependencies last.
"""
from app.db.base import Base  # noqa: F401

# ── Tier 1: no foreign keys ────────────────────────────────────────────────────
from app.models.user import User              # noqa: F401, E402
from app.models.contact import Contact        # noqa: F401, E402
from app.models.company import Company        # noqa: F401, E402
from app.models.batch_job import BatchJob     # noqa: F401, E402

# ── Tier 2: FK → tier 1 ───────────────────────────────────────────────────────
from app.models.refresh_token import RefreshToken  # noqa: F401, E402
from app.models.search import Search               # noqa: F401, E402

# ── Tier 3: FK → tier 2 ───────────────────────────────────────────────────────
from app.models.verification_result import VerificationResult  # noqa: F401, E402

# ── Tier 4: FK → tier 3 ───────────────────────────────────────────────────────
from app.models.evidence_source import EvidenceSource  # noqa: F401, E402
from app.models.job_result import JobResult            # noqa: F401, E402
