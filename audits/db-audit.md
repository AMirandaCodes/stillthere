# Database Security Audit

**Date:** 2026-07-07  
**Scope:** All database interactions — ORM models, repositories, session config, migrations, docker-compose, environment secrets, caching layer  
**Files examined:** `backend/app/db/session.py`, `backend/app/core/config.py`, `backend/app/repositories/*.py`, `backend/app/models/*.py`, `backend/alembic/versions/*.py`, `docker-compose.yml`, `.env.example`, `.env.prod.example`, `backend/app/core/logging.py`, `backend/app/services/rate_limit_service.py`

---

## Summary Risk Score: 3.5 / 10

**No SQL injection. No plaintext secrets in git. No string concatenation in queries. No unbounded table scans.** The ORM layer is used consistently and correctly throughout. The risks are operational: hardcoded fallback credentials in source, unauthenticated Redis in development, PII accumulation in a debug column, and no PostgreSQL-native tenant isolation. None are exploitable remotely against a correctly deployed production instance.

---

## Findings

---

### DB-01 — Medium — CWE-312: Cleartext Storage of Sensitive Information

**Title:** `raw_search_data` JSONB column accumulates scraped PII indefinitely with no retention limit

**Evidence:**
- `backend/app/models/verification_result.py:68`  
  ```python
  raw_search_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
  ```
  Comment: *"Raw Serper + fetched page data retained for debugging and re-analysis"*
- `backend/alembic/versions/001_initial_schema.py:226-234`: column defined with no expiry
- No code path anywhere nullifies or deletes this column after processing is done

**Why it matters:**  
`raw_search_data` stores the full Serper API JSON response plus scraped page fragments. Those payloads routinely include names, email addresses, phone numbers, and physical addresses from public pages. Every verification row carries this indefinitely. If the database is breached, the attacker gets not just StillThere's schema but a collection of scraped personal profiles.

**Exploitability:** Low complexity once DB access is obtained. No external attack vector, but the blast radius of any DB breach is disproportionate to operational need (the field exists "for debugging").

**Remediation:**

Option A — Null the field after the pipeline completes (minimal, preserves schema):
```python
# In apply_pipeline_result (result_mapper.py), after writing evidence:
verification_result.raw_search_data = None
```

Option B — Add a scheduled purge (more robust):
```python
# A Celery beat task or cron:
await session.execute(
    update(VerificationResult)
    .where(
        VerificationResult.status.in_(["complete", "failed"]),
        VerificationResult.updated_at < datetime.now(timezone.utc) - timedelta(days=7),
        VerificationResult.raw_search_data.isnot(None),
    )
    .values(raw_search_data=None)
)
```

Option C (long term) — Remove the column entirely; store only structured `evidence_sources`.

---

### DB-02 — Medium — CWE-522: Insufficiently Protected Credentials

**Title:** Hardcoded fallback credentials in source code; docker-compose uses them as defaults

**Evidence:**
- `backend/app/core/config.py:23–32`:
  ```python
  SECRET_KEY: str = "change-me-in-production"
  DATABASE_URL: str = "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification"
  REDIS_URL: str = "redis://localhost:6379/0"
  ```
- `docker-compose.yml:9–11`:
  ```yaml
  POSTGRES_USER: ${POSTGRES_USER:-cvp_user}
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-cvp_password}
  ```
- `docker-compose.yml:45`:
  ```yaml
  DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-cvp_user}:${POSTGRES_PASSWORD:-cvp_password}@db:...
  ```

**Why it matters:**  
If `.env` is absent, misconfigured, or the file fails to load, the application starts successfully with the hardcoded password `cvp_password`. The validator in `config.py:117–138` only rejects the `SECRET_KEY` placeholder in `APP_ENV=production` — there is no guard against the placeholder `DATABASE_URL`. A developer who forgets to create `.env` silently gets a working app against the credentials in source.

The credentials themselves (`cvp_user` / `cvp_password`) are also in `.env.example` (committed), so they are effectively public knowledge for anyone with repo access.

**Exploitability:** Requires access to the running host or ability to reach port 5432, but the credentials are known to every developer with git access.

**Remediation:**

In `config.py`, force the variables to be set explicitly rather than providing fallback values:
```python
# Remove defaults so startup fails fast if the env file is missing:
DATABASE_URL: str  # No default — must be in .env
REDIS_URL: str
CELERY_BROKER_URL: str
CELERY_RESULT_BACKEND: str
```

Add a startup guard analogous to the `SECRET_KEY` check:
```python
@model_validator(mode="after")
def validate_production_settings(self) -> "Settings":
    if "cvp_password" in self.DATABASE_URL and self.APP_ENV == "production":
        raise ValueError("DATABASE_URL contains the default dev password in production.")
    ...
```

In `docker-compose.yml`, remove the `:-cvp_password` fallback so the compose file fails rather than silently using known credentials:
```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}   # no fallback
```

---

### DB-03 — Low — CWE-400: Uncontrolled Resource Consumption

**Title:** LIKE metacharacters in contact search input not escaped — enables expensive table scans

**Evidence:**
- `backend/app/repositories/contact_repository.py:81–93`:
  ```python
  normalised = query.strip().lower()
  base = (
      select(Contact)
      .where(Contact.normalized_name.ilike(f"%{normalised}%"))
  )
  ```

**Why it matters:**  
SQLAlchemy's `ilike()` correctly parameterizes the full value as a bind parameter — this is not SQL injection. However, LIKE `_` (single-char wildcard) and `%` (multi-char wildcard) in the user's search term ARE interpreted by the PostgreSQL pattern engine. A search for `___________` (11 underscores) creates a pattern `%____________%` — an expensive backtracking match across every row. As the `contacts` table grows this becomes a denial-of-service vector.

**PoC:** `GET /api/v1/contacts?search=______________________________`

**Remediation:**
```python
# contact_repository.py:81
def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

normalised = _escape_like(query.strip().lower())
base = select(Contact).where(
    Contact.normalized_name.ilike(f"%{normalised}%", escape="\\")
)
```

---

### DB-04 — Low — CWE-284: Improper Access Control

**Title:** No PostgreSQL Row Level Security — tenant isolation is application-layer-only

**Evidence:**  
No RLS policies in any migration file (`backend/alembic/versions/001–005`). Tenant filtering is applied in service methods (`batch_service.py`, `verification_repository.py`, `contact_service.py`) but not enforced by the database engine itself.

**Why it matters:**  
A future developer who adds an admin endpoint, a one-off data script, or a direct Alembic `op.execute()` in a migration can read any user's data without triggering an application-layer check. Defense only exists at one layer.

**Remediation (defence-in-depth, not a critical fix):**

```sql
-- In a new migration, after the users table exists:
ALTER TABLE searches ENABLE ROW LEVEL SECURITY;
ALTER TABLE batch_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY search_owner ON searches
    USING (user_id = current_setting('app.current_user_id', true)::uuid
           OR current_setting('app.bypass_rls', true) = 'true');
```

The app would need to `SET LOCAL app.current_user_id = :id` at the start of each session. The migration user bypasses via `app.bypass_rls = true`. This is a significant implementation change — flag it as a medium-term hardening goal rather than an immediate fix.

---

### DB-05 — Low — CWE-306: Missing Authentication for Critical Function

**Title:** Redis has no authentication in development — exposes Celery results and rate limit counters

**Evidence:**
- `docker-compose.yml:24–35`: Redis service has no `requirepass`, no ACL config, no `bind` restriction
- `docker-compose.yml:29–30`: Port `"6379:6379"` binds to `0.0.0.0`
- `backend/app/services/rate_limit_service.py:54–55`: `if not self._redis: return True, 0, reset_at` — fail-open, so zeroing counters has same effect as Redis being down

**Why it matters:**  
On any shared or office network, `redis-cli -h <developer-host> -p 6379` connects without a password. An attacker can:
1. `DEL stillthere:rl:user:*:*` — reset all rate limit counters, then exhaust Serper/Anthropic quotas
2. Read Celery result keys to enumerate job UUIDs
3. `FLUSHDB` — erase broker queue and result backend, stalling all background jobs

**Remediation:**

In `docker-compose.yml`:
```yaml
redis:
  image: redis:7-alpine
  command: redis-server --requirepass "${REDIS_PASSWORD:-changeme-dev-only}"
  ports:
    - "127.0.0.1:6379:6379"   # localhost only
```

Add `REDIS_PASSWORD` to `.env.example` and update the `REDIS_URL` / `CELERY_BROKER_URL` entries to include the password:
```
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
```

---

### DB-06 — Low — CWE-668: Exposure of Resource to Wrong Sphere

**Title:** PostgreSQL and Redis ports bound to all interfaces in development docker-compose

**Evidence:**
- `docker-compose.yml:15`: `"5432:5432"` → binds to `0.0.0.0:5432`
- `docker-compose.yml:29`: `"6379:6379"` → binds to `0.0.0.0:6379`

**Why it matters:**  
Any machine on the same LAN as the developer's workstation can reach PostgreSQL and Redis directly. Combined with known fallback credentials (DB-02) and unauthenticated Redis (DB-05), this is a complete network-accessible DB exposure in office or shared environments.

**Remediation:** Bind to localhost only:
```yaml
ports:
  - "127.0.0.1:5432:5432"
  - "127.0.0.1:6379:6379"
```

Note: `docker-compose.prod.yml` already configures PostgreSQL and Redis without exposed ports (internal network only). This fix is for the development compose file only.

---

### DB-07 — Low — CWE-272: Least Privilege Violation

**Title:** Single database user `cvp_user` used for both application runtime and schema migrations

**Evidence:**  
- `backend/alembic/env.py:22`: `config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)` — Alembic uses the same `DATABASE_URL` as the application
- `backend/app/db/session.py:12`: same URL for the runtime engine
- `docker-compose.yml:45`, `backend/app/core/config.py:26`: single credential set throughout

**Why it matters:**  
A compromised application credential (e.g., through a future vulnerability) can be used to run DDL: `DROP TABLE users`, `ALTER TABLE users ADD COLUMN backdoor TEXT`, `GRANT ... TO attacker_user`. Runtime queries only need `SELECT / INSERT / UPDATE / DELETE` on application tables plus `USAGE` on sequences.

**Remediation:**  
Create a second PostgreSQL role in the `init-test-db.sql` or an initial migration:
```sql
CREATE ROLE cvp_migrate LOGIN PASSWORD '...';
GRANT ALL ON ALL TABLES IN SCHEMA public TO cvp_migrate;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO cvp_migrate;

-- Application role (runtime only)
REVOKE CREATE ON SCHEMA public FROM cvp_user;
REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM cvp_user;
```

Set `MIGRATION_DATABASE_URL` separately in `.env` for Alembic; keep `DATABASE_URL` as the restricted runtime credential.

This is a significant operational change — schedule it as a hardening milestone rather than an immediate fix.

---

### DB-08 — Low — CWE-532: Insertion of Sensitive Information into Log File

**Title:** SQLAlchemy `echo=True` in DEBUG mode may log query parameter values including PII

**Evidence:**
- `backend/app/db/session.py:17`: `echo=settings.DEBUG`
- `backend/app/core/config.py:21`: `DEBUG: bool = False` (default off)
- `.env.example:9`: `DEBUG=true`

**Why it matters:**  
`echo=True` logs every SQL statement. With asyncpg, bind parameters are sometimes expanded in the log output, which means `WHERE email = $1` with `$1='user@example.com'` may appear in plaintext in stdout. In a containerized environment where `docker compose logs` output is accessible to all developers, this leaks contact emails and any other column values from WHERE clauses and INSERT statements. `hashed_password` values in INSERT logs are not plaintext passwords but they would still be logged.

**Remediation:**

Replace the blanket `echo=settings.DEBUG` with a separate flag:
```python
# config.py
DATABASE_ECHO: bool = False  # Never default to True; explicit opt-in only

# session.py
echo=settings.DATABASE_ECHO,
```

Silence SQLAlchemy at WARNING in `logging.py`:
```python
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

---

### DB-09 — Informational — CWE-missing: No Data Retention or Deletion Mechanism for PII

**Title:** Personal data (name, email, verification history) accumulates indefinitely

**Evidence:**  
No DELETE endpoint for contacts, searches, or verification results exists in any route file. No Celery beat task schedules data purge. No retention window is configured. `raw_search_data` (DB-01) is the most acute instance of this broader absence.

**Why it matters:**  
GDPR Article 17 (right to erasure) and CCPA require that personal data is deleted on request. Without a deletion mechanism, a compliant response to a data subject request requires a manual DBA operation.

**Remediation (design sketch, not an immediate code fix):**

1. Add `DELETE /api/v1/verifications/{id}` (owner only) that deletes `VerificationResult` and cascades to `EvidenceSource`. The CASCADE constraints are already wired.
2. Consider whether `Contact` records should be deletable or anonymized (set `email=NULL`, `full_name='[deleted]'`).
3. Add `VERIFICATION_RETENTION_DAYS` setting and a Celery beat task that nulls `raw_search_data` and deletes `failed` results older than the threshold.

---

### DB-10 — Informational — Absence of Structured Audit Trail

**Title:** Privileged operations (admin bulk export, all-verifications view) are not audit-logged

**Evidence:**  
`backend/app/api/v1/routes/admin.py`: `list_all_verifications` logs nothing about who accessed it. `backend/app/api/v1/routes/batch.py`: `export_batch_results` logs nothing about data exports. `backend/app/services/auth_service.py`: login success and failure are not explicitly logged.

**Why it matters:**  
If an admin account is compromised and used to extract data via `GET /api/v1/admin/verifications`, there is no audit trail to detect it or determine scope.

**Remediation (low-effort starting point):**

```python
# In admin route:
logger.info(
    "admin.verifications.list",
    admin_user_id=str(_.id),
    offset=pagination.offset,
    limit=pagination.page_size,
)

# In export route:
logger.info(
    "batch.export",
    user_id=str(current_user.id),
    job_id=str(job_id),
)

# In auth_service.login():
logger.info("auth.login.success", user_id=str(user.id))
# (already raises AuthError on failure — log that too in the route handler)
```

---

## Top 5 Prioritised Fixes (fastest risk reduction)

| Priority | Finding | File | Change |
|---|---|---|---|
| 1 | DB-02 | `config.py:23–32` | Remove hardcoded `DATABASE_URL` and `SECRET_KEY` defaults; fail startup if absent |
| 2 | DB-05 | `docker-compose.yml:24–35` | Add `requirepass` to Redis; bind port to `127.0.0.1` |
| 3 | DB-06 | `docker-compose.yml:15,29` | Bind PostgreSQL to `127.0.0.1:5432` |
| 4 | DB-01 | `result_mapper.py` | Null `raw_search_data` after pipeline completes |
| 5 | DB-03 | `contact_repository.py:81–83` | Escape `%` and `_` in contact search before LIKE |

---

## Checklist Diff

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | Parameterized queries / ORM | ✅ PASS | All queries use SQLAlchemy ORM or Core; zero string concatenation in SQL |
| 2 | Connection string security | ⚠️ PARTIAL | `.env` gitignored; hardcoded fallback credentials in `config.py` and compose defaults (DB-02) |
| 3 | DB user permissions (least privilege) | ⚠️ PARTIAL | Single `cvp_user` for app + migrations; cannot verify PG ACLs from code (DB-07) |
| 4 | Sensitive data encryption at rest | ⚠️ PARTIAL | Passwords bcrypt-hashed; tokens SHA-256-hashed; no column encryption; `raw_search_data` unencrypted (DB-01) |
| 5 | PII handling compliance | ❌ FAIL | No deletion endpoint, no retention policy, `raw_search_data` accumulates (DB-01, DB-09) |
| 6 | Query timeout configurations | ✅ PASS | `command_timeout=10` on asyncpg; applies to both QueuePool and NullPool engines |
| 7 | Connection pool settings | ✅ PASS | `pool_size=10`, `max_overflow=20`; NullPool for Celery tasks |
| 8 | Transaction handling for consistency | ✅ PASS | `get_db()` auto-commit/rollback; explicit `session.commit()` in services; BL-01 fix merged atomically |
| 9 | Audit logging for sensitive operations | ❌ FAIL | No structured audit trail for admin access, exports, or login events (DB-10) |
| 10 | NoSQL injection hardening | N/A | Redis not queried with user input; rate-limit keys use hashed IDs and dates |
| 11 | Row/Tenant isolation | ⚠️ PARTIAL | App-layer filtering only; no PostgreSQL RLS policies (DB-04) |
| 12 | Least-privilege networking | ❌ FAIL (dev) | PG and Redis bound to `0.0.0.0` in `docker-compose.yml` (DB-06); ✅ PASS in `docker-compose.prod.yml` |
| 13 | TLS in transit | ✅ PASS (prod) | `fix_database_url` converts `sslmode=require` to asyncpg `ssl=require`; `rediss://` handled in `celery_app.py` |
| 14 | Secret management & rotation | ⚠️ PARTIAL | `.env` gitignored; no secrets manager; no rotation mechanism; hardcoded fallbacks (DB-02) |
| 15 | Schema & integrity controls | ✅ PASS | FK with `ondelete`, NOT NULL on all critical columns, CHECK constraints on enum columns, UUID PKs |
| 16 | Field-level minimization | ✅ PASS | ORM `selectinload` with explicit relation paths; no `SELECT *`; admin view loads only what it displays |
| 17 | Pagination & query limits | ✅ PASS | `page_size` capped at 100 (`le=100`); all list queries have `.offset().limit()` |
| 18 | Backup/restore security | ❓ UNABLE TO VERIFY | Docker volume `postgres_data` locally; Render managed backups (paid tier only) |
| 19 | Data retention & deletion | ❌ FAIL | No deletion endpoints, no purge jobs, no retention window (DB-09) |
| 20 | Migrations safety | ⚠️ PARTIAL | `downgrade()` defined in all 5 migrations; same credential as runtime; no dry-run configured |
| 21 | ORM raw-query escape hatch review | ✅ PASS | No `text()` with user input; `sa.text("now()")` only in server defaults |
| 22 | LIKE / regex input handling | ⚠️ PARTIAL | `contact_repository.py:81–83` embeds user input in LIKE without escaping `%`/`_` (DB-03) |
| 23 | Query timeouts & resource guards | ✅ PASS | `command_timeout=10` enforced at driver level; `MAX_BATCH_SIZE=50` limits queue depth |
| 24 | Audit & monitoring depth | ❌ FAIL | structlog to stdout only; no centralized/immutable audit log |
| 25 | PII in logs/metrics | ⚠️ PARTIAL | `echo=settings.DEBUG` may log query bind values (DB-08); httpx/httpcore silenced |
| 26 | Indexing of sensitive data | ✅ PASS | `ix_refresh_tokens_token_hash` indexes the hash, not raw token; no plaintext secrets indexed |
| 27 | Service/account lifecycle | ❓ UNABLE TO VERIFY | Single `cvp_user` shared account; no IAM/rotation visible in code |
| 28 | Caching layers | ❌ FAIL (dev) | Redis has no password and is publicly exposed in `docker-compose.yml` (DB-05) |
| 29 | Analytics/ETL exports | ⚠️ PARTIAL | CSV export is auth-gated (ownership checked); no PII masking; no export audit log (DB-10) |
