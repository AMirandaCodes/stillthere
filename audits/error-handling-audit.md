# Error Handling Audit

**Date:** 2026-07-06  
**Scope:** Full stack — FastAPI backend, Celery workers, React frontend  
**Files examined:** `main.py`, `app/api/deps/auth.py`, `app/api/v1/routes/{auth,verifications,batch}.py`, `app/services/{auth_service,verification_service,llm_service,search_service,evidence_service}.py`, `app/tasks/{pipeline,verification_tasks,batch_tasks}.py`, `app/core/{auth,utils,rate_limiting}.py`, `frontend/src/services/{api,authService,verificationService,batchService}.ts`, `frontend/src/context/AuthContext.tsx`, `frontend/src/pages/{HomePage,VerificationResultPage,BatchJobsPage,SearchHistoryPage}.tsx`

---

## Summary

The backend global error handler is solid, and the pipeline's async error isolation is generally well-structured. The two most critical gaps are: (1) the LLM service swallows API failures and writes a misleading COMPLETE result, and (2) there is no React ErrorBoundary anywhere — a render-time exception crashes the entire UI. Several secondary gaps exist around auth token management and user-facing error messaging.

---

## Pattern Inventory

| Layer | Mechanism | Notes |
|---|---|---|
| Backend global | `@app.exception_handler(Exception)` → 500 | Catches all unhandled; safe message only |
| Rate limit | `@app.exception_handler(RateLimitExceeded)` → 429 | Correct |
| Auth route | `ValueError` string codes → `HTTPException` | Stringly-typed (see E-03) |
| Batch route | `BatchValidationError` → `HTTPException(400)` | Clean typed pattern |
| Other routes | Direct `HTTPException` for 404s | Consistent |
| JWT auth dep | `JWTError` → `HTTPException(401)` | Correct (see E-11 edge case) |
| Pipeline tasks | `except Exception` → `FAILED` status + log | Correct, but LLM bypass defeats it (E-01) |
| LLM service | `except Exception` → all-unclear defaults | **Does not raise** — silently masks failure |
| Frontend API | Axios 401 → clear tokens → redirect | No refresh attempt (E-05) |
| Frontend forms | `try/catch` on submit → `setServerError` | Consistent across all pages |
| Frontend queries | `PageState` component renders `error` | Does not cover render-time errors (E-02) |

---

## Findings

---

### E-01 — LLM failure silently produces COMPLETE result with all-UNCLEAR fields
**Importance: 8/10**

**Location:** `backend/app/services/llm_service.py:121–130` (`analyse` method)

`LLMService.analyse()` catches all exceptions internally and returns an all-UNCLEAR `LLMAnalysisResult` with an empty `evidence_sources` list:

```python
# llm_service.py — current
except Exception as exc:
    logger.error("LLM API call failed", error=str(exc))
    return LLMAnalysisResult(raw_response=f"API error: {exc}")  # all fields default to UNCLEAR
```

`execute_pipeline` (`pipeline.py`) receives this silently-failed object, runs confidence scoring (0 field score + 0 source score = **0/LOW**), and returns a `PipelineResult`. The task then writes `status=COMPLETE` to the database.

**Result in the UI:** The user sees Verification = COMPLETE, all five fields UNCLEAR, Confidence = 0/LOW. There is no indication that anything failed. A broken `ANTHROPIC_API_KEY` or an Anthropic rate limit produces the same outcome as a contact genuinely having no online presence.

**Fix:** Let the API exception propagate. The task layer already has a correct `except Exception` handler that writes `FAILED` status:

```python
# backend/app/services/llm_service.py
async def analyse(self, name, company, email, search_results, pages) -> LLMAnalysisResult:
    """
    Raises on API/network failure (caller writes FAILED status).
    Returns all-unclear defaults only on JSON parse failure (LLM responded but gave bad output).
    """
    prompt = self.build_prompt(name, company, email, search_results, pages)
    try:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.error("LLM API call failed", error=str(exc))
        raise  # propagate — task layer writes FAILED status
    raw = message.content[0].text
    return self._parse_response(raw)  # parse failure still returns all-unclear (acceptable)
```

No changes needed in `pipeline.py` or the task files — their `except Exception` blocks already handle this correctly.

---

### E-02 — No React ErrorBoundary — render crash whites out the entire app
**Importance: 8/10**

**Location:** No `ErrorBoundary` exists anywhere in `frontend/src/`

`PageState` handles async query errors, but React renders can throw synchronously for reasons `PageState` cannot catch: a type mismatch from an API schema change, `null` dereference on `data.evidence_sources.map(...)`, or a library bug. Without an `ErrorBoundary`, any such exception crashes the full React tree. The user sees a blank page with no recovery path except a manual reload.

**Fix — create `frontend/src/components/ErrorBoundary.tsx`:**

```tsx
import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 text-center">
          <p className="text-lg font-semibold text-gray-800">Something went wrong</p>
          <p className="max-w-sm text-sm text-gray-500">{this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-white hover:bg-brand-700"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
```

**Wrap the outlet in `frontend/src/components/layout/Layout.tsx`:**

```tsx
import { ErrorBoundary } from "@/components/ErrorBoundary";

// Inside Layout's return, wherever <Outlet /> currently appears:
<ErrorBoundary>
  <Outlet />
</ErrorBoundary>
```

This covers every page with a single wrapping point. Errors in individual pages are contained; the nav and layout remain intact.

---

### E-03 — Stringly-typed ValueError codes in AuthService
**Importance: 7/10**

**Location:** `backend/app/services/auth_service.py`, `backend/app/api/v1/routes/auth.py`

`AuthService` raises `ValueError("email_exists")`, `ValueError("invalid_credentials")`, etc. Routes match these with string equality:

```python
# routes/auth.py — current
except ValueError as exc:
    if str(exc) == "email_exists":          # typo → falls through to bare raise
        raise HTTPException(status_code=409, ...)
    raise                                   # unrecognized ValueError → 500 via global handler
```

A single-character typo in either file (`"email_exist"` vs `"email_exists"`) silently produces a 500 instead of the correct 4xx. Adding a new error code in the service without updating the route is undetectable at import time.

**Fix — add a typed exception class to `auth_service.py`:**

```python
# backend/app/services/auth_service.py

class AuthError(Exception):
    """Domain error from AuthService. Routes map .code to an HTTPException."""
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
```

Replace every `raise ValueError(...)` in `AuthService` with `raise AuthError(...)`, then update routes:

```python
# backend/app/api/v1/routes/auth.py
from app.services.auth_service import AuthError, AuthService

@router.post("/register", ...)
async def register(request: Request, payload: UserCreate, db: DbSession) -> User:
    try:
        return await AuthService(db).register(payload.email, payload.full_name, payload.password)
    except AuthError as exc:
        match exc.code:
            case "email_exists":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="An account with this email address already exists")
            case _:
                raise  # unknown AuthError code → still a 500, but now intentional
```

The same pattern applies to `login` and `refresh`. `AuthError` is now a distinct type — the IDE catches mismatches, and a bare `raise` is explicit rather than accidental.

---

### E-04 — CSV export error silently swallowed in BatchJobsPage
**Importance: 6/10**

**Location:** `frontend/src/pages/BatchJobsPage.tsx:handleExport`

```tsx
// current
async function handleExport(jobId: string) {
  setExporting(jobId);
  try {
    await batchService.exportCsv(jobId);
  } finally {
    setExporting(null);  // error falls through here with no user notification
  }
}
```

Any failure (network error, session expiry, server error) silently resets the spinner. The user has no way to know the export failed.

**Fix:**

```tsx
const [exportError, setExportError] = useState<string | null>(null);

async function handleExport(jobId: string) {
  setExporting(jobId);
  setExportError(null);
  try {
    await batchService.exportCsv(jobId);
  } catch (err) {
    setExportError(err instanceof Error ? err.message : "Export failed. Please try again.");
  } finally {
    setExporting(null);
  }
}
```

Render `exportError` beneath the first export button that triggered it, or as a shared error banner above the job list.

---

### E-05 — Hard logout on expired access token; refresh token is never tried
**Importance: 6/10**

**Location:** `frontend/src/services/api.ts:response interceptor`

```ts
// current
if (error.response?.status === 401) {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  if (window.location.pathname !== "/login") window.location.href = "/login";
}
```

Any 401 immediately invalidates both tokens and redirects to `/login`, even when a valid 7-day refresh token exists. Users must re-authenticate every 30 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`).

**Fix — attempt one silent refresh before giving up:**

```ts
// frontend/src/services/api.ts
import axios from "axios";
import type { TokenResponse } from "@/types/auth";

let isRefreshing = false;

// ... existing interceptors unchanged above this ...

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config as typeof error.config & { _retry?: boolean };

    if (error.response?.status === 401 && !original._retry && !isRefreshing) {
      original._retry = true;
      isRefreshing = true;
      try {
        const refreshToken = localStorage.getItem(REFRESH_KEY);
        if (!refreshToken) throw new Error("no refresh token");
        const res = await axios.post<TokenResponse>("/api/v1/auth/refresh", {
          refresh_token: refreshToken,
        });
        localStorage.setItem(ACCESS_KEY, res.data.access_token);
        localStorage.setItem(REFRESH_KEY, res.data.refresh_token);
        original.headers = original.headers ?? {};
        original.headers.Authorization = `Bearer ${res.data.access_token}`;
        return api(original);
      } catch {
        localStorage.removeItem(ACCESS_KEY);
        localStorage.removeItem(REFRESH_KEY);
        if (window.location.pathname !== "/login") window.location.href = "/login";
      } finally {
        isRefreshing = false;
      }
    }

    // ... rest of existing error message extraction unchanged ...
  }
);
```

---

### E-06 — Malformed JWT `sub` claim causes 500 instead of 401
**Importance: 5/10**

**Location:** `backend/app/api/deps/auth.py:44`

After validating the JWT signature and confirming `sub` is non-empty, the code calls `UUID(user_id_str)` without a try/except:

```python
# current
user_id_str: str | None = payload.get("sub")
if not user_id_str:
    raise HTTPException(status_code=401, detail="Malformed token")

user = await UserRepository(db).get_by_id(UUID(user_id_str))  # ValueError if sub is not a UUID
```

If `sub` is present but not a valid UUID string (e.g., a token issued by a different system, or a manually crafted token), `UUID(user_id_str)` raises `ValueError`. That `ValueError` is not caught here, propagates through FastAPI, and hits the global `Exception` handler → **500**.

**Fix:**

```python
# backend/app/api/deps/auth.py
try:
    user_id = UUID(user_id_str)
except ValueError:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Malformed token",
        headers={"WWW-Authenticate": "Bearer"},
    )
user = await UserRepository(db).get_by_id(user_id)
```

---

### E-07 — AuthContext clears valid tokens on transient network error
**Importance: 5/10**

**Location:** `frontend/src/context/AuthContext.tsx:useEffect`

```tsx
// current
authService
  .getMe()
  .then(setUser)
  .catch(() => authService.clearTokens())  // any error — including Wi-Fi blip — logs user out
  .finally(() => setIsLoading(false));
```

A momentary network failure during the app's initial session-restore call silently clears perfectly valid tokens. The user is logged out through no fault of their own, with no error message.

**Fix:** Only clear tokens on an explicit 401 response; preserve them on network errors:

```tsx
// frontend/src/context/AuthContext.tsx
authService
  .getMe()
  .then(setUser)
  .catch((err: unknown) => {
    const msg = err instanceof Error ? err.message.toLowerCase() : "";
    // Clear only on auth rejection, not on network failure
    if (msg.includes("401") || msg.includes("unauthorized") || msg.includes("not authenticated")) {
      authService.clearTokens();
    }
    // Otherwise: keep tokens; user will get the proper 401 on their next API call
  })
  .finally(() => setIsLoading(false));
```

> Note: the current fix relies on error message text because `api.ts` throws `new Error(String(message))` without attaching a status code. A more robust long-term improvement is to attach `.status` to thrown errors in `api.ts` and inspect that property here instead.

---

### E-08 — Internal class name leaks into user-visible error messages
**Importance: 5/10**

**Location:** `backend/app/core/utils.py:format_exc_message`, callers in `verification_tasks.py:_run_verification_async` and `batch_tasks.py:_process_batch_row_async`

```python
# utils.py
def format_exc_message(exc: Exception, max_len: int = 500) -> str:
    return f"{type(exc).__name__}: {exc}"[:max_len]
```

When a `_PipelineError` is caught, `format_exc_message` produces:

```
_PipelineError: All search queries failed — verify SERPER_API_KEY is correct
```

This string is stored in `VerificationResult.error_message` and rendered directly in `VerificationResultPage.tsx`:

```tsx
{data.error_message && <p className="mt-1 text-sm text-red-600">{data.error_message}</p>}
```

Users see the private underscore-prefixed class name and an internal API-key hint that means nothing to them.

**Fix — add a translation step before storage in both task files:**

```python
# backend/app/tasks/verification_tasks.py  (and same pattern in batch_tasks.py)
from app.tasks.pipeline import _PipelineError

def _user_error_message(exc: Exception) -> str:
    if isinstance(exc, _PipelineError):
        return "Search failed. The service may be temporarily unavailable — please try again."
    return "An unexpected error occurred during verification."
```

Replace the `error_msg = format_exc_message(exc)` line with:

```python
error_msg = _user_error_message(exc)
logger.error(
    "Pipeline execution failed",
    result_id=result_id,
    exc_type=type(exc).__name__,
    error=str(exc),
    traceback=traceback.format_exc(),
)
```

Full exception detail stays in logs; only the friendly string reaches the DB and UI.

---

### E-09 — No request correlation ID; 500 errors are untraceable by users
**Importance: 5/10**

**Location:** `backend/app/main.py:unhandled_exception_handler`

The 500 response body is:

```json
{"detail": "An internal error occurred. Please try again later."}
```

There is no `request_id` or `trace_id`. A user reporting "I got an error" cannot give support anything to search logs with. The log line exists (`logger.error("Unhandled exception", path=..., error=..., exc_info=True)`) but there is no bridge between the user's report and that line.

**Fix — add a lightweight request ID middleware:**

```python
# backend/app/main.py
import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        req_id = uuid.uuid4().hex[:8]
        structlog.contextvars.bind_contextvars(request_id=req_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = req_id
        return response

# Register before SlowAPIMiddleware:
app.add_middleware(RequestIDMiddleware)
```

Update the exception handler to include the ID:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    req_id = structlog.contextvars.get_contextvars().get("request_id", "unknown")
    logger.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again later.",
            "request_id": req_id,
        },
    )
```

Users can quote the `request_id` from the browser Network tab when reporting issues.

---

### E-10 — Verification polling permanently stops on the first transient network error
**Importance: 4/10**

**Location:** `frontend/src/pages/VerificationResultPage.tsx:refetchInterval`

```tsx
// current
refetchInterval: (query) => {
  if (query.state.error) return false;   // stops forever on any error
  const status = query.state.data?.status;
  return status === "complete" || status === "failed" ? false : 2000;
},
```

A single transient network error (Wi-Fi blip, server restart) permanently halts polling. The user sees the "Verifying…" spinner indefinitely; the page never self-recovers without a manual reload.

**Fix — allow a few consecutive errors before stopping:**

```tsx
refetchInterval: (query) => {
  if ((query.state.errorUpdateCount ?? 0) > 3) return false;  // give up after 4 consecutive failures
  const status = query.state.data?.status;
  return status === "complete" || status === "failed" ? false : 2000;
},
```

`errorUpdateCount` is provided by TanStack Query and resets to zero when a successful fetch occurs.

---

### E-11 — Serper 429 responses are not retried
**Importance: 4/10**

**Location:** `backend/app/services/search_service.py:_is_retriable`

```python
def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500  # 429 is not included
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))
```

A 429 from Serper.dev raises `httpx.HTTPStatusError` with status 200 > 429 < 500, so it is not retried. The failed query is logged and skipped. Near quota exhaustion this means multiple queries fail, reducing evidence quality. If all four queries fail, the pipeline raises `_PipelineError` (→ FAILED status).

**Fix:**

```python
def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))
```

Also extend the backoff window so 429 retries don't immediately hit the limit again:

```python
@retry(
    retry=retry_if_exception(_is_retriable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=60),  # was max=10
    reraise=True,
)
async def _fetch_query(self, query_text: str) -> dict[str, Any]:
    ...
```

---

## Priority Matrix

| ID | Finding | Importance | Category | Effort |
|---|---|---|---|---|
| E-01 | LLM failure → COMPLETE with all-UNCLEAR | 8/10 | Correctness | Low |
| E-02 | No React ErrorBoundary | 8/10 | Recovery | Low |
| E-03 | Stringly-typed ValueError codes | 7/10 | Consistency | Low |
| E-04 | Export error silently swallowed | 6/10 | User feedback | Low |
| E-05 | Hard logout on expired access token | 6/10 | Recovery | Medium |
| E-06 | UUID parse not guarded → 500 vs 401 | 5/10 | Categorization | Trivial |
| E-07 | AuthContext clears tokens on network error | 5/10 | Recovery | Low |
| E-08 | Internal class name in user error message | 5/10 | Information | Low |
| E-09 | No request correlation ID in 500 | 5/10 | Observability | Low |
| E-10 | Polling stops on transient error | 4/10 | Recovery | Trivial |
| E-11 | Serper 429 not retried | 4/10 | Recovery | Trivial |

---

## What is already well-handled

- **Global exception handler** (`main.py`) — correctly catches all unhandled exceptions, logs with `exc_info=True`, returns a safe message with no stack trace exposure.
- **Rate limit handler** — registered separately from the generic `Exception` handler; returns clean 429 with a human message.
- **JWT auth dep** (`deps/auth.py`) — correctly catches `JWTError`, returns 401 with `WWW-Authenticate` header. `auto_error=False` on `HTTPBearer` prevents a 403 when no token is provided.
- **`get_optional_user`** — silently returns `None` instead of raising; correct for guest-accessible routes.
- **Pipeline task error handling** — `_check_and_set_running` idempotency guard, crash recovery (evidence cleanup on RUNNING restart), all wrapped in `except Exception` → FAILED.
- **Search failure isolation** — individual Serper query failures are logged and skipped; only total failure raises `_PipelineError`.
- **Evidence fetch isolation** — `_fetch_one` never raises; `asyncio.gather` with `return_exceptions=False` is safe because the inner method is infallible.
- **LLM JSON parse failure** — `_parse_response` tries three progressively more lenient strategies and always returns a valid `LLMAnalysisResult`. This is correct (parse failure ≠ API failure).
- **Frontend form errors** — every form page uses react-hook-form + zod for field-level validation with visible field errors, plus a `serverError` state rendered inline for API errors.
- **`BatchValidationError`** — the one clean example of a typed domain exception; correctly mapped to 400 in the batch route.
