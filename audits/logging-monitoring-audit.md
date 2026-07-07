# Logging and Monitoring Security Audit

**Date:** 2026-07-07  
**Scope:** All logging configuration, structured log fields, security event coverage, log storage, and monitoring infrastructure  
**Files examined:** `app/core/logging.py`, `app/core/config.py`, `app/main.py`, `app/db/session.py`, `app/services/search_service.py`, `app/services/llm_service.py`, `app/services/evidence_service.py`, `app/services/auth_service.py`, `app/services/verification_service.py`, `app/services/rate_limit_service.py`, `app/api/v1/routes/auth.py`, `app/api/deps/auth.py`, `app/tasks/verification_tasks.py`, `app/tasks/batch_tasks.py`, `app/core/circuit_breakers.py`, `docker-compose.yml`

---

## Summary Risk Score: 4.0 / 10

Solid foundation: structlog with JSON output in production, request IDs threaded through all log entries, Celery task lifecycle events logged at key stages, and unhandled exceptions caught globally. The main gaps are: PII (name, email) emitted in INFO/WARNING fields on every verification run, a Redis connection URL that can contain a cleartext password being logged at startup, and a complete absence of security event logging for authentication failures — failed logins and 401/403 events leave no log trace, preventing forensic detection of account attacks.

---

## Findings

---

### LM-01 — Medium — CWE-532: Insertion of Sensitive Information into Log File

**Title:** Redis connection URL (including password) logged in plaintext at startup

**Evidence:**
`backend/app/main.py:38`:
```python
logger.info("Redis connected", url=settings.REDIS_URL)
```

`settings.REDIS_URL` is the full connection string. In development this is `redis://redis:6379/0` — no credentials. In production (Upstash free tier), the CLAUDE.md and `.env.prod.example` document that `REDIS_URL` uses `rediss://` and contains a password:
```
rediss://default:AbCdEfGhIjKlMnOpQrSt@hostname.upstash.io:6380/0
```

This full string, including the password token, is written to stdout in every deployment's startup logs.

**Why it matters:**
Render retains logs accessible to all team members with project access. Any log export, screenshot, or log forwarding pipeline will contain the Redis password in plaintext. An attacker with log access (or who intercepts a log export) can authenticate to the Redis instance directly and access session data, search result caches, and rate-limit counters.

**Exploitability:** Requires log read access (Render project access, or exfiltration of a log export). Low barrier if the team shares project credentials, or if logs are forwarded to a third-party service with less access control.

**Remediation:**

Redact credentials before logging:
```python
# app/main.py — in lifespan(), replace line 38

from urllib.parse import urlparse, urlunparse

def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password:
        safe_netloc = f"{parsed.hostname}:{parsed.port}" if parsed.port else (parsed.hostname or "")
        return urlunparse(parsed._replace(netloc=safe_netloc))
    return url

logger.info("Redis connected", url=_redact_url(settings.REDIS_URL))
```

Move this helper to `app/core/logging.py` if needed elsewhere (e.g., for `DATABASE_URL` which contains credentials in the same format).

---

### LM-02 — Medium — CWE-778: Insufficient Logging

**Title:** Failed login attempts, 401/403 authorization failures, and input validation errors generate no log events — no forensic audit trail

**Evidence:**

**Failed logins — not logged:**
`backend/app/api/v1/routes/auth.py:55–61`:
```python
except AuthError as exc:
    match exc.code:
        case AuthError.INVALID_CREDENTIALS:
            raise HTTPException(status_code=401, ...)
```
`backend/app/services/auth_service.py:63–66`:
```python
if not user:
    dummy_verify(password)
    raise AuthError(AuthError.INVALID_CREDENTIALS)
if not verify_password(password, user.hashed_password):
    raise AuthError(AuthError.INVALID_CREDENTIALS)
```
Neither site calls `logger.warning()`. There is no log entry for a failed login.

**401/403 — not logged:**
`backend/app/api/deps/auth.py:25–71`: every `HTTPException` branch silently raises with no log call. These include: missing token, expired/invalid JWT, user not found, session invalidated, and admin check failure.

**Global handler exclusion:** `main.py:149`:
```python
@app.exception_handler(Exception)
```
`HTTPException` (FastAPI's domain exception for 4xx/5xx responses) is handled by Starlette's own built-in handler, which is registered before any user exception handler. It never reaches the `Exception` handler above. All 401, 403, 422, and 429 events are completely invisible in logs.

**Why it matters:**
A targeted brute-force attack against a specific user account generates zero log events. With the rate limiter set to 10/min (login), an attacker can test 14,400 passwords per day with no log trace. The only retrospective evidence is the rate limiter's Redis counter — which auto-expires daily and leaves no permanent record.

NIST SP 800-53 AU-2 requires logging of authentication attempts. Without this, there is no way to detect compromised accounts after the fact, satisfy audit requirements, or trigger IP-based blocking.

**Remediation:**

Add a structlog call before each significant security event raise:

```python
# In app/services/auth_service.py

from app.core.logging import get_logger
logger = get_logger(__name__)

async def login(self, email: str, password: str) -> TokenResponse:
    user = await self._users.get_by_email(email)
    if not user:
        dummy_verify(password)
        logger.warning("auth.login.failed", reason="user_not_found", email=email)
        raise AuthError(AuthError.INVALID_CREDENTIALS)
    if not verify_password(password, user.hashed_password):
        logger.warning("auth.login.failed", reason="wrong_password", user_id=str(user.id))
        raise AuthError(AuthError.INVALID_CREDENTIALS)
    if not user.is_active:
        logger.warning("auth.login.failed", reason="inactive", user_id=str(user.id))
        raise AuthError(AuthError.INACTIVE)
    logger.info("auth.login.success", user_id=str(user.id))
    ...
```

Note: log `user_id` (UUID) for wrong-password cases rather than `email` — the UUID identifies the account without embedding PII in the log line. For user-not-found cases, the email must be logged to identify the attempt target, but mask it:
```python
masked = email[:2] + "***@" + email.split("@")[-1]  # "jo***@example.com"
logger.warning("auth.login.failed", reason="user_not_found", email_masked=masked)
```

Add an HTTPException handler that logs 401/403:
```python
# In app/main.py, after the rate_limit_handler

from fastapi.exceptions import HTTPException as FastAPIHTTPException

@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException) -> JSONResponse:
    if exc.status_code in (401, 403):
        logger.warning(
            "auth.access_denied",
            path=request.url.path,
            method=request.method,
            status=exc.status_code,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

---

### LM-03 — Medium — CWE-532: Insertion of Sensitive Information into Log File

**Title:** Contact name and work email logged as structured fields on every verification run

**Evidence:**

`backend/app/services/search_service.py:170–176`:
```python
logger.info(
    "Search complete",
    name=name,       # ← full_name from VerificationCreate
    company=company,
    queries_run=len(combined.queries_run),
    total_hits=combined.total_hits,
)
```
This INFO log fires on every successful search. Production uses `JSONRenderer`, so the output is:
```json
{"event": "Search complete", "name": "John Smith", "company": "Acme Corp", ...}
```

`backend/app/services/search_service.py:143–148`:
```python
logger.warning(
    "Search query failed — skipping",
    query=query_text,    # ← e.g. '"john.smith@acme.com"' or '"John Smith" "Acme Corp"'
    query_type=query_type,
    error=str(exc),
)
```
`query_text` for the email search query is literally `'"work@email.com"'` — the user's email address. This fires at WARNING level whenever the email query fails (common, since many emails don't return Serper results).

`backend/app/services/search_service.py:191`:
```python
logger.debug("Search cache hit", query=query_text)
```
Debug: only in `DEBUG=True`. Still logs the full email.

**Why it matters:**
Every verification creates at minimum one INFO log line containing the subject's full name and employer. Failed queries add WARNING lines containing the work email. Over time, logs accumulate a database of every contact verified — names, companies, and email addresses. This creates:
1. A PII dataset in logs that is not subject to the same access controls as the PostgreSQL database
2. A GDPR Article 25 (data minimisation at design) violation if the application is subject to EU law
3. A secondary exfiltration path: an attacker who gains log access gets a record of all verified contacts

**Remediation:**

Replace identifying values with opaque identifiers in log fields:

```python
# search_service.py — search() method

logger.info(
    "Search complete",
    # name=name,         ← remove
    # company=company,   ← remove
    queries_run=len(combined.queries_run),
    total_hits=combined.total_hits,
)
```

For the failure warning, log only the query type and error — not the full query string:
```python
logger.warning(
    "Search query failed — skipping",
    # query=query_text,   ← remove
    query_type=query_type,
    error=str(exc),
)
```

If the query text is needed for debugging, log a hash instead:
```python
import hashlib
query_hash = hashlib.sha256(query_text.encode()).hexdigest()[:8]
logger.warning("Search query failed — skipping", query_hash=query_hash, query_type=query_type, error=str(exc))
```

The same principle applies to the `build_prompt()` path — the full prompt text is not logged anywhere currently, which is correct. Keep it that way.

---

### LM-04 — Low — CWE-532: Insertion of Sensitive Information into Log File

**Title:** SQLAlchemy query echo logs bind parameters (email addresses in WHERE clauses) when `DEBUG=True`

**Evidence:**
`backend/app/db/session.py:17`:
```python
engine = create_async_engine(
    settings.DATABASE_URL,
    ...
    echo=settings.DEBUG,
)
```

`backend/app/db/session.py:35`:
```python
_task_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    ...
    echo=settings.DEBUG,
)
```

When `DEBUG=True`, SQLAlchemy emits every SQL statement to the Python `logging` module at `INFO` level, including the bound parameter values. A query like:
```python
select(User).where(User.email == email)
```
logs as:
```
SELECT users.email ... WHERE users.email = 'john.smith@acme.com'
```

Any email passed to `get_by_email()`, `email_exists()`, or `get_or_create_by_email()` appears in plaintext in debug logs.

**Why it matters:**
In production `DEBUG=False`, so echo is off — this is not a production issue. The concern is developer machines and staging environments: local dev logs contain plaintext emails from every database query, meaning developer terminal sessions or Docker log files on shared machines become a PII leakage path. If developers export or share logs for debugging, they implicitly share contact data.

**Exploitability:** Low — development environments only. No production impact with `DEBUG=False`.

**Remediation:**

Disable echo unconditionally, even in debug. The Celery task engine is most sensitive — task workers running in debug mode log every email lookup:
```python
# app/db/session.py — remove echo entirely
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    connect_args=_CONNECT_ARGS,
    # echo=settings.DEBUG,  ← remove
    future=True,
)
```

If SQL-level debugging is occasionally needed, use `echo="debug"` as a one-time environment variable passed inline to docker compose rather than baking it into `DEBUG` mode:
```bash
DEBUG=False SQLALCHEMY_ECHO=1 docker compose up --build backend
```
And conditionally in `session.py`:
```python
import os
echo = os.environ.get("SQLALCHEMY_ECHO", "").lower() in ("1", "true")
engine = create_async_engine(..., echo=echo, ...)
```

---

### LM-05 — Low — CWE-306: Missing Authentication for Critical Function

**Title:** Celery Flower dashboard exposed on port 5555 without authentication — task arguments and worker state visible to any network peer

**Evidence:**
`docker-compose.yml:120–141`:
```yaml
flower:
  ports:
    - "5555:5555"
  command: celery -A app.tasks.celery_app flower --port=5555
```

Flower's default configuration has no authentication. The dashboard is bound to `0.0.0.0:5555` — accessible to anyone on the same network (LAN, VPN, or Docker bridge depending on the host). The Flower UI shows:
- All task names, UUIDs, arguments, results, and timing
- All worker names, concurrency, and queue depths
- A "Revoke task" button that allows cancelling running verifications

Task arguments include `result_id` and `job_result_id` UUIDs. While these are not directly sensitive, the task timestamps reveal when users submit verifications.

**Why it matters:**
On a developer's local machine, Flower is exposed on `127.0.0.1:5555` via Docker's port mapping. On an office network or shared host, it's accessible to all peers. The revoke function allows an unauthenticated actor to cancel any running verification task.

**Exploitability:** Low — requires network access to the host running Docker, and task arguments (UUIDs) are the only information exposed. Impact is task disruption, not data exfiltration.

**Remediation:**

Add basic auth to Flower:
```yaml
# docker-compose.yml
flower:
  command: celery -A app.tasks.celery_app flower --port=5555 --basic_auth=admin:${FLOWER_PASSWORD:-changeme}
```

Add `FLOWER_PASSWORD` to `.env.example`:
```
FLOWER_PASSWORD=change-me-before-exposing-to-network
```

Or restrict binding to localhost only (no remote access):
```yaml
ports:
  - "127.0.0.1:5555:5555"   # bind to loopback only
```

In production on Render, Flower should be a separate Web Service with Render's built-in basic auth or not deployed at all on the free tier.

---

### LM-06 — Low — CWE-778: Insufficient Logging

**Title:** Rate limit exceeded events (HTTP 429) not logged — quota exhaustion by a single actor leaves no evidence

**Evidence:**
`backend/app/main.py:141–146`:
```python
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down."},
    )
```

No `logger` call is present. A client hitting the login endpoint 10 times per minute (or the verification endpoint repeatedly) generates zero log output.

**Why it matters:**
Rate limiting is the primary brute-force protection. When it fires, that is evidence of an attack. Without logging, there is no way to detect systematic quota abuse, identify which IPs are hitting limits, or correlate rate limit events with account compromise attempts.

**Remediation:**
```python
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    logger.warning(
        "rate_limit.exceeded",
        path=request.url.path,
        method=request.method,
        client_ip=request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
                  or (request.client.host if request.client else "unknown"),
    )
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please slow down."})
```

---

### LM-07 — Low — CWE-778: Insufficient Logging

**Title:** Input validation failures (HTTP 422) not logged — malformed requests and fuzzing attempts leave no trace

**Evidence:**
FastAPI handles Pydantic `RequestValidationError` with a built-in handler that returns HTTP 422 directly. This handler is not overridable via `@app.exception_handler(Exception)` — it's registered by Starlette at startup and takes priority. No custom handler for `RequestValidationError` exists in `main.py`.

A bot sending malformed JSON bodies (e.g., missing required fields, wrong types, overly long strings) to every endpoint receives 422 responses with no log entry.

**Why it matters:**
422 floods are a common reconnaissance pattern: fuzzers probe API endpoints with invalid input to discover field names, type constraints, and error message formats. Without logging, there is no signal that a fuzzing campaign is underway.

**Remediation:**
```python
# app/main.py

from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info(
        "request.validation_error",
        path=request.url.path,
        method=request.method,
        errors=len(exc.errors()),   # count only; don't log the full body
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )
```

Do not log `exc.body` (the raw request body) as it may contain passwords from malformed login attempts.

---

### LM-08 — Informational — No Log Retention Beyond 24 Hours on Render Free Tier

**Title:** Render free tier retains logs for 24 hours only — all security events are unrecoverable after one day

**Evidence:**
`app/core/logging.py:13`:
```python
handler = logging.StreamHandler(sys.stdout)
```

All logs go to stdout (no file sink). Docker Compose dev: stored in Docker's json-file logging driver (default 100 MB cap, no time-based rotation). Render production: retained for 24 hours on the free tier (documented in Render's pricing page).

**Why it matters:**
An account compromise discovered on day 3 has no logs from the attack day. GDPR Article 33 requires breach notification within 72 hours — without 72+ hours of logs, the scope of a breach cannot be assessed.

**Recommendation (priority: defer to paid tier or add log forwarding):**
Options in order of cost:
1. **Log forwarding to Logtail/Papertrail free tier** (free up to 1 GB/month, 7-day retention): add a log drain in Render settings → External Logging → `https://in.logtail.com/?source_token=TOKEN`
2. **Sentry** (free tier, 5000 events/month): captures exceptions with full context and retains for 30 days. Add with:
   ```bash
   pip install sentry-sdk[fastapi]
   ```
   ```python
   # app/main.py
   import sentry_sdk
   if settings.SENTRY_DSN:
       sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.APP_ENV)
   ```
3. Upgrade to Render Starter tier ($7/mo) for 7-day log retention.

---

### LM-09 — Informational — No External Error Tracking or Alerting

**Title:** No Sentry, PagerDuty, or equivalent — production failures require manual log polling to detect

**Evidence:**
No `sentry_sdk`, `rollbar`, `bugsnag`, or equivalent import exists anywhere in the codebase. The only error visibility is the global exception handler logging to stdout:
```python
logger.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
```
No metric collection (Prometheus, StatsD), no uptime monitoring beyond the Docker healthchecks, and no alerting configuration.

**Why it matters:**
A production exception spike (circuit breaker trips on every request, database connection pool exhausted, Celery queue backed up) is only visible if someone actively watches Render logs. The `restart: unless-stopped` Docker policy and Render's health checks will restart crashed services, but the root cause goes undiagnosed until the next manual log review.

**Recommendation:**
Sentry free tier (5000 events/month) covers this use case with zero infrastructure overhead:
```python
# app/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        integrations=[FastApiIntegration(), CeleryIntegration()],
        traces_sample_rate=0.1,  # 10% of requests for performance monitoring
    )
```

Add `SENTRY_DSN: str = ""` to `Settings`. Leave blank to disable in development.

---

## Top 5 Prioritised Fixes

| Priority | Finding | File | Change | Impact |
|---|---|---|---|---|
| 1 | LM-02 | `auth_service.py`, `main.py` | Log failed logins (`logger.warning`) and add `HTTPException` handler for 401/403 | Enables breach detection |
| 2 | LM-01 | `main.py:38` | Redact password from `settings.REDIS_URL` before logging | Prevents credential leakage |
| 3 | LM-03 | `search_service.py:170–176`, `search_service.py:143–148` | Remove `name=`, `company=`, `query=` from log fields | Removes PII from every log line |
| 4 | LM-06 | `main.py:141–146` | Add `logger.warning()` in `rate_limit_handler` | Makes brute-force visible |
| 5 | LM-04 | `session.py:17`, `session.py:35` | Remove `echo=settings.DEBUG` from both engines | Stops email leakage in dev |

---

## Logging Compliance Checklist

| # | Item | Status | Notes |
|---|---|---|---|
| 1a | Passwords not logged | ✅ PASS | `password` never appears in any log call; bcrypt hash never logged |
| 1b | Tokens not logged | ✅ PASS | Raw refresh tokens and access tokens never logged; only UUIDs and hashes |
| 1c | PII not logged | ❌ FAIL | Name and company logged at INFO on every search (`search_service.py:170`); email logged at WARNING on query failures (`search_service.py:144`) |
| 1d | API keys not logged | ⚠️ PARTIAL | Anthropic/Serper keys not logged directly; Redis URL (can contain password) logged at startup (LM-01) |
| 1e | Credit card numbers | N/A | No payment processing |
| 2a | Failed login attempts logged | ❌ FAIL | Zero log output for incorrect credentials (LM-02) |
| 2b | Authorization failures logged | ❌ FAIL | 401/403 from `get_current_user()`/`get_current_admin()` silently discarded (LM-02) |
| 2c | Input validation failures logged | ❌ FAIL | 422 Pydantic errors handled by FastAPI's built-in handler, no custom logging (LM-07) |
| 2d | System errors logged | ✅ PASS | Global `unhandled_exception_handler` covers unhandled Python exceptions with `logger.error()` and `exc_info=True` |
| 2e | Rate limit events logged | ❌ FAIL | `RateLimitExceeded` handler returns 429 with no log call (LM-06) |
| 2f | Task pipeline failures logged | ✅ PASS | Both `verification_tasks.py` and `batch_tasks.py` log pipeline errors with `exc_type`, `error`, and `traceback` fields |
| 2g | Circuit breaker events | ⚠️ PARTIAL | Circuit breaker state changes are not logged (no `logger.warning` in `record_failure()` or `is_open()`); only downstream errors that trip the breaker are logged |
| 3a | Input sanitization in logs | ✅ PASS | Log values pass through structlog's `JSONRenderer` in production, which JSON-encodes all values; newlines and special characters are escaped |
| 3b | Structured logging | ✅ PASS | structlog used throughout with named key-value fields; `JSONRenderer` in production, `ConsoleRenderer` in debug |
| 3c | Request ID correlation | ✅ PASS | `RequestIDMiddleware` in `main.py:80–89` binds `request_id` to structlog contextvars; propagates to all log lines within a request |
| 4a | Secure log storage | ⚠️ PARTIAL | Logs to stdout (no file), stored by Docker json-file driver or Render; not encrypted, access controlled only by platform RBAC |
| 4b | Log rotation policy | ⚠️ PARTIAL | Docker's json-file driver has no configured size/count limits (`max-size`, `max-file` not set); Render auto-manages but truncates at 24h |
| 4c | Log retention | ❌ FAIL | Render free tier: 24-hour rolling window only (LM-08); no external log forwarding configured |
| 4d | Log backup strategy | ❌ FAIL | No log forwarding to Logtail, Papertrail, CloudWatch, or equivalent |
| 5a | Unusual activity detection | ❌ FAIL | No alerting system; no anomaly detection (LM-09) |
| 5b | Error rate monitoring | ❌ FAIL | No Sentry, no Prometheus metrics, no dashboards (LM-09) |
| 5c | Performance anomalies | ❌ FAIL | No APM, no latency tracking beyond Celery task timing visible in Flower |
| 5d | Flower task monitor secured | ❌ FAIL | Flower exposed on 0.0.0.0:5555 without authentication (LM-05) |
| —  | SQLAlchemy echo (PII in dev) | ❌ FAIL | `echo=settings.DEBUG` logs bind params including email addresses in dev/staging (LM-04) |
