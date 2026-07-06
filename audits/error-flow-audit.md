# Error Flow Audit

**Date:** 2026-07-06  
**Scope:** Five critical error paths traced end-to-end across FastAPI backend, Celery workers, and React frontend.  
**Method:** Static code analysis of actual source; no invented paths or functions.

---

## Error Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ ORIGIN POINTS                                                   │
│                                                                 │
│  [DB]          [Serper API]    [Anthropic API]    [Browser]     │
│  SQLAlchemy    httpx timeout   anthropic SDK      User/Network  │
└──────┬──────────────┬──────────────┬─────────────────┬─────────┘
       │              │              │                 │
       ▼              ▼              ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ TRANSFORMATION LAYERS                                           │
│                                                                 │
│  API ROUTES (FastAPI)                                           │
│  ├─ get_db() rollback + re-raise ──────────────────────────────►│
│  ├─ HTTPException (auth/validation/404) ────────────────────────►│
│  ├─ AuthError → match(exc.code) → HTTPException ────────────────►│
│  └─ Uncaught → global unhandled_exception_handler (500) ────────►│
│                                                                 │
│  CELERY TASKS (Workers)                                         │
│  ├─ _PipelineError → _user_error_message() → VerificationResult│
│  │  .status=FAILED + .error_message (user-friendly)            │
│  ├─ Other Exception → _user_error_message() → FAILED           │
│  └─ Phase-1 DB failure → propagates → task re-queues → ??─────►│ ← EF-05
│                                                                 │
│  FRONTEND (Axios interceptor → React)                           │
│  ├─ 401 → attempt refresh → retry OR → /login redirect         │
│  ├─ 4xx/5xx → extract detail → new Error(string)              │
│  └─ Pydantic 422 → join .msg fields → new Error(string)        │
└──────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ FINAL HANDLING POINTS                                           │
│                                                                 │
│  HTTP clients        → JSON {detail, request_id?} or 2xx body  │
│  Polling pages       → error_message field from FAILED result   │
│  ErrorBoundary       → "Something went wrong" + retry button    │
│  Auth context        → token cleared + redirect to /login       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Path Analysis

### Path 1: Database Connection Failure

| Step | Location | Behaviour |
|---|---|---|
| Caught | `session.py:47–51` `get_db()` | `except Exception → rollback → raise` |
| Transformed | `main.py:99–109` | Global handler → 500 JSON `{detail, request_id}` |
| Logged | `main.py:102` | `logger.error("Unhandled exception", ...)` |
| User sees | Browser | "An internal error occurred. Please try again later." + `request_id` |
| State consistent | Yes (API) | Rollback ensures no partial write |
| State consistent | **No (Worker)** | Phase-1 DB failure leaves result stuck in PENDING — **see EF-05** |

### Path 2: Third-party API Timeout

| Service | Layer | Retry | On exhaustion |
|---|---|---|---|
| Serper | `search_service.py:169–174` | 3× exponential, 429 + 5xx + network | Skip query; if all fail → `_PipelineError` |
| Evidence (scrape) | `evidence_service.py:94–111` | 2× network only | `fetch_ok=False`; pipeline continues |
| Anthropic | `llm_service.py:153–163` | **None** | Exception propagates → FAILED — **see EF-03** |

### Path 3: Invalid User Input

| Input | Caught by | HTTP response | User sees |
|---|---|---|---|
| Pydantic field error | FastAPI automatic | 422 + `{detail: [{loc,msg,type}]}` | Field-level message (joined by `api.ts`) |
| Missing CSV columns | `batch_service.py:78–84` | 400 via `BatchValidationError` | Exact missing column names |
| Non-UTF-8 file | `batch_service.py:153–155` | 400 via `BatchValidationError` | "File must be UTF-8 encoded." |
| CSV > 5 MB | `batch_service.py:149–150` | 400 via `BatchValidationError` | Size limit message |
| File read into memory first | `batch_service.py:148` | **OOM before check** | Server crash — **see EF-04** |

### Path 4: Authentication Failure

| Failure | Code path | Status | Header |
|---|---|---|---|
| No token | `deps/auth.py:25–30` | 401 | `WWW-Authenticate: Bearer` ✓ |
| Invalid/expired JWT | `deps/auth.py:32–38` | 401 | `WWW-Authenticate: Bearer` ✓ |
| Non-UUID `sub` (current user) | `deps/auth.py:44–51` | 401 | `WWW-Authenticate: Bearer` ✓ |
| Non-UUID `sub` (optional user) | `deps/auth.py:92` | **500** | — — **see EF-01** |
| User not found / inactive | `deps/auth.py:53–56` | 401 | **Missing** — **see EF-07** |
| Bad credentials | `routes/auth.py:46–50` | 401 | — |
| Inactive account | `routes/auth.py:52–55` | 403 | — |
| Bad refresh token | `routes/auth.py:65–68` | 401 | — |

### Path 5: File / Streaming Errors

| Scenario | Code path | Behaviour |
|---|---|---|
| DB error during CSV export setup | `batch_service.py:360–362` | Exception propagates → 500 before streaming starts |
| DB error mid-stream (offset > 0) | `batch_service.py:374–386` | Connection closed; **client receives truncated CSV with HTTP 200** — **see EF-02** |
| `io.StringIO` write failure | n/a | Impossible; in-memory buffer |
| Client disconnects mid-stream | Starlette handles | Generator is abandoned; no state inconsistency |

---

## Findings

### EF-01 — `get_optional_user` has unguarded UUID parse → 500

**Severity: 7/10**  
**File:** `backend/app/api/deps/auth.py:92`

`get_optional_user` is the silent-failure variant of `get_current_user`. The E-06 fix guarded the UUID parse in `get_current_user` but left the same call in `get_optional_user` unprotected. A JWT with a syntactically valid but non-UUID `sub` claim (e.g. `"sub": "not-a-uuid"`) causes `UUID(user_id_str)` to raise `ValueError`, which escapes the dependency and becomes an unhandled 500. `submit_verification` uses `OptionalUser`, so this is reachable without authentication.

**Failure scenario:** attacker sends `POST /api/v1/verifications` with a crafted JWT whose `sub` is `"not-a-uuid"` → 500 instead of graceful `None` return.

**Fix:**
```python
# deps/auth.py — get_optional_user, after line 91
user_id_str: str | None = payload.get("sub")
if not user_id_str:
    return None
try:
    user_id = UUID(user_id_str)
except ValueError:
    return None
user = await UserRepository(db).get_by_id(user_id)
```

---

### EF-02 — CSV export silently truncates on mid-stream DB failure

**Severity: 7/10**  
**File:** `backend/app/services/batch_service.py:361–387`

`export_csv_stream` is a `StreamingResponse` async generator. FastAPI sends `200 OK` and response headers before the generator yields its first byte. If a `SQLAlchemyError` occurs at any loop iteration after `offset=0` (e.g. DB connection drops, statement timeout), the exception propagates out of the generator; Starlette closes the TCP connection abruptly. The client receives a truncated CSV with `HTTP 200` and no indication of failure. Depending on the HTTP client, the file appears to download successfully but is missing rows.

**Failure scenario:** DB connection is lost after the first 100-row page is yielded. Client receives a "complete" 100-row file that silently omits the remaining rows.

**Fix** — wrap the inner query in a try/except and stop the generator cleanly:
```python
# Inside the while True loop in export_csv_stream:
try:
    page_rows = list((await session.execute(stmt)).scalars().all())
except Exception as exc:
    logger.error("CSV export DB error", job_id=str(job_id), offset=offset, error=str(exc))
    break  # stop generator; headers already sent, but at least no more data is implied
```

A more robust solution is to pre-count rows and validate the session before starting streaming, then use a dedicated error row as the final yield if the loop breaks early. However, HTTP semantics make it impossible to change the status code after headers are committed, so the break is the best achievable without a buffered (non-streaming) export.

---

### EF-03 — Anthropic API call has no request timeout

**Severity: 6/10**  
**File:** `backend/app/services/llm_service.py:154–159`

`self._client.messages.create(...)` uses the SDK default timeout of 600 seconds (10 minutes). A single hung or very slow Anthropic response blocks the Celery worker process for the full 10 minutes. With `--concurrency=1` on Render's free tier, this stalls every queued verification for the duration. There is no watchdog or per-task timeout at the Celery layer either.

**Failure scenario:** Anthropic API is slow; one verification task hangs for 10 minutes; all other queued verifications time out waiting; users see "pending" for up to 10 minutes per queued verification.

**Fix:**
```python
# llm_service.py — messages.create call
message = await self._client.messages.create(
    model=self._model,
    max_tokens=_MAX_TOKENS,
    system=_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": prompt}],
    timeout=30.0,          # ← add this; 30 s is generous for a 1024-token response
)
```

The Anthropic SDK accepts a `timeout` float (seconds) directly on `messages.create`. Alternatively set at client construction: `anthropic.AsyncAnthropic(api_key=..., timeout=30.0)`.

---

### EF-04 — Large file buffered fully into memory before size check

**Severity: 4/10**  
**File:** `backend/app/services/batch_service.py:148–150`

```python
raw = await file.read()         # ← full file read into memory
if len(raw) > _MAX_CSV_BYTES:   # ← check happens after
    raise BatchValidationError("File exceeds the 5 MB size limit.")
```

Starlette's `UploadFile.read()` buffers the entire multipart body in memory (or in a temp file for very large uploads, depending on Starlette version and `spool_max_size`). A malicious or accidental 200 MB upload causes a large memory spike before the guard rejects it. Under the current Render free-tier memory limit, this could kill the process.

**Fix** — stream-read with a cap:
```python
MAX = _MAX_CSV_BYTES
chunks: list[bytes] = []
total = 0
while chunk := await file.read(65536):
    total += len(chunk)
    if total > MAX:
        raise BatchValidationError("File exceeds the 5 MB size limit.")
    chunks.append(chunk)
raw = b"".join(chunks)
```

---

### EF-05 — Task Phase-1 DB failure leaves VerificationResult stuck in PENDING

**Severity: 5/10**  
**Files:** `backend/app/tasks/verification_tasks.py:56–76`, `backend/app/tasks/batch_tasks.py:165–199`

The task flow has three DB sessions. If Session 1 (the idempotency guard / RUNNING transition) raises a `SQLAlchemyError`, the exception propagates out of `_check_and_set_running` and out of `asyncio.run()`. With `acks_late=True`, the Celery broker re-queues the message for retry. If the DB remains unavailable until max retries are exhausted, the `VerificationResult` is abandoned in `PENDING` state. The user's result page shows a spinner indefinitely with no error ever displayed.

**Failure scenario:** brief DB restart during heavy load; several tasks fail Phase 1; after retries exhaust, their results are permanently PENDING; users never see FAILED with an error message.

**Fix** — catch DB failure in the task's sync entry point and update the result status out-of-band (or accept the limitation and document it). A minimal approach:
```python
# run_verification (sync entry point)
def run_verification(self, result_id: str) -> None:
    try:
        asyncio.run(_run_verification_async(result_id))
    except Exception as exc:
        # Phase-1 DB failure: try one best-effort FAILED update via a fresh connection.
        logger.error("Task-level DB failure", result_id=result_id, error=str(exc))
        try:
            asyncio.run(_mark_failed_direct(result_id, "Verification could not be processed."))
        except Exception:
            pass  # DB truly unavailable; accept stuck-PENDING as the failure mode
        raise  # re-raise so Celery re-queues if within retry limit
```

Where `_mark_failed_direct` opens a fresh `TaskSessionLocal` session and does a direct `UPDATE` without loading the full object. This limits the blast radius to complete DB unavailability.

---

### EF-06 — `AuthContext.logout()` does not clear local state on network failure

**Severity: 5/10**  
**File:** `frontend/src/context/AuthContext.tsx:48–51`

```tsx
async function logout() {
    const refreshToken = authService.getRefreshToken() ?? "";
    await authService.logout(refreshToken);   // ← can throw
    setUser(null);                            // ← never reached on throw
}
```

If the backend is unavailable when the user clicks Logout, `authService.logout()` throws (network error or 5xx). `setUser(null)` is never called, so `user` remains set and the UI stays in the authenticated state. The user cannot log out until the backend recovers.

**Failure scenario:** backend is redeploying on Render (30-second cold start); user clicks Logout; appears to fail; user is still "logged in" on the client and confused.

**Fix:**
```tsx
async function logout() {
    const refreshToken = authService.getRefreshToken() ?? "";
    try {
        await authService.logout(refreshToken);
    } catch {
        // Server-side revocation failed; clear local state anyway.
        // The refresh token will expire naturally on the backend.
    }
    authService.clearTokens();
    setUser(null);
}
```

---

### EF-07 — Missing `WWW-Authenticate` header on two 401 responses

**Severity: 3/10**  
**File:** `backend/app/api/deps/auth.py:42`, `backend/app/api/deps/auth.py:53–56`

RFC 7235 §4.1 requires a `WWW-Authenticate` challenge header on every 401 response. Two paths in `get_current_user` omit it:

```python
# Line 42: empty sub claim
raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")
#                                                              ↑ no headers=

# Lines 53–56: user not found or inactive
raise HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
)
#                                                     ↑ no headers=
```

OAuth2-aware clients (Swagger UI, some API clients) expect this header to understand the auth scheme required.

**Fix:** Add `headers={"WWW-Authenticate": "Bearer"}` to both raises.

---

## Anti-pattern Summary

| Anti-pattern | Location | Finding |
|---|---|---|
| Swallowed exception | `AuthContext.tsx:48–51` | logout() drops network error | EF-06 |
| Generic catch hiding specific errors | `_user_error_message()` in both task files | `_PipelineError` vs other exceptions produce same user message | Low severity, intentional |
| Missing error boundary (HTTP layer) | `batch_service.py:361–387` | No error signalling in streaming body | EF-02 |
| Errors used for flow control | `auth_service.py` + `routes/auth.py` | `AuthError` — typed domain error, not flow control. **Not an anti-pattern.** | n/a |
| Inconsistent error format | `deps/auth.py:42`, `53–56` | 401 without `WWW-Authenticate` | EF-07 |
| No defence at boundary | `batch_service.py:148` | File fully buffered before size check | EF-04 |
| Missing timeout | `llm_service.py:154` | No request timeout on Anthropic call | EF-03 |
| Partial terminal state | `verification_tasks.py` | Phase-1 DB failure → stuck PENDING | EF-05 |

---

## Standardized Error Handling Template

### Backend route (FastAPI)

```python
@router.post("/resource")
async def create_resource(payload: ResourceCreate, db: DbSession) -> ResourceResponse:
    # 1. Input validation: handled automatically by Pydantic (422)
    # 2. Domain errors: use typed exceptions, map to HTTP here
    try:
        return await ResourceService(db).create(payload)
    except DomainError as exc:
        match exc.code:
            case "already_exists":
                raise HTTPException(status_code=409, detail="Resource already exists")
            case "not_found":
                raise HTTPException(status_code=404, detail="Resource not found")
            case _:
                raise  # unknown code → global 500 handler
    # 3. Unexpected exceptions fall to global unhandled_exception_handler → 500 + request_id
```

### Backend service / repository

```python
# Domain errors → typed exception (never ValueError with string codes)
class DomainError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

# DB errors → do NOT catch here; let get_db() rollback and re-raise
# External API errors → catch at the service boundary; re-raise or wrap in DomainError
```

### Celery task

```python
@celery_app.task(bind=True, acks_late=True)
def run_task(self, record_id: str) -> None:
    try:
        asyncio.run(_run_async(record_id))
    except Exception as exc:
        logger.error("Task failed at entry", record_id=record_id, error=str(exc))
        # Best-effort terminal state update
        try:
            asyncio.run(_mark_failed(record_id, "Could not process request."))
        except Exception:
            pass
        raise  # re-queue if within retry limit

async def _run_async(record_id: str) -> None:
    # Phase 1: idempotency + RUNNING transition (own session)
    # Phase 2: load data (own session, read-only)
    # Phase 3: call external APIs — catch and convert to FAILED here
    try:
        result = await run_pipeline(...)
    except Exception as exc:
        error_msg = _user_error_message(exc)
        logger.error("Pipeline failed", error=str(exc), traceback=traceback.format_exc())
    # Phase 4: write results (own session)
```

### Frontend API call

```tsx
// Services: always let axios error propagate as Error(string) via interceptor
// Call sites: catch only what you can handle; let ErrorBoundary catch the rest
async function handleAction() {
    setLoading(true);
    setError(null);
    try {
        const result = await service.doThing();
        setData(result);
    } catch (err) {
        setError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
        setLoading(false);
    }
}

// Render error: always render error state if error !== null
{error && (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
    </div>
)}
```

---

## Findings by Severity

| ID | Severity | Title |
|---|---|---|
| EF-01 | **7/10** | `get_optional_user` unguarded UUID → 500 |
| EF-02 | **7/10** | CSV export silently truncates on mid-stream DB error |
| EF-03 | **6/10** | No timeout on Anthropic API call |
| EF-04 | **4/10** | Large file buffered into memory before size check |
| EF-05 | **5/10** | Task Phase-1 DB failure leaves result stuck in PENDING |
| EF-06 | **5/10** | `logout()` doesn't clear client state on network failure |
| EF-07 | **3/10** | Missing `WWW-Authenticate` on two 401 responses |
