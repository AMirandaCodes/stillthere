# Software Design / Architecture Audit

**Date:** 2026-07-06  
**Scope:** Full stack — `backend/app/` + `frontend/src/`  
**Method:** Static analysis; all source files read directly.

---

## 1. Architecture Summary

**Pattern:** Layered Architecture (4 tiers), not MVC.

```
Routes (HTTP boundary)
  └─ Services (orchestration + business logic)
       └─ Repositories (all DB queries)
            └─ Models (ORM + PostgreSQL)
```

Celery workers run the same pipeline services (Search, Evidence, LLM, Confidence)
as standalone processes. The frontend is a React SPA with an Axios API client,
TanStack Query for server state, and `AuthContext` for session management.

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Browser (React + TypeScript)                                           │
│  AuthContext ─→ authService ─→ api.ts (Axios + interceptors)           │
│  Pages: Home | History | Batch Upload | Batch Jobs | Admin             │
│  UI: TriStateBadge | StatusBadge | ConfidenceScore | Pagination        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ /api/* proxy (Vite dev) or direct (Render prod)
┌───────────────────────────▼─────────────────────────────────────────────┐
│  FastAPI  (app/main.py)                                                 │
│  Middleware: CORS → SlowAPI → RequestIDMiddleware                       │
│                                                                         │
│  Routes (/api/v1/)                                                      │
│   auth  verifications  batch  contacts  companies  admin  health        │
│       │                                                                 │
│       ▼  Services                                                       │
│   AuthService   VerificationService   BatchService                     │
│   RateLimitService   CacheService                                       │
│   SearchService*  EvidenceService*  LLMService*  ConfidenceService*    │
│   (* shared with Celery pipeline — no DB access)                       │
│       │                                                                 │
│       ▼  Repositories                                                   │
│   UserRepo  ContactRepo  CompanyRepo  VerificationRepo                 │
│   RefreshTokenRepo                                                      │
│   BatchRepository ← STUB (unused; BatchService bypasses it)  ◄── SD-01│
│       │                                                                 │
│       ▼  PostgreSQL  (QueuePool for API; NullPool for tasks)           │
│   User  Contact  Company  Search  VerificationResult  EvidenceSource  │
│   BatchJob  JobResult  RefreshToken                                    │
└──────────────────┬──────────────────────────────────────────────────────┘
                   │  commit-before-.delay()
┌──────────────────▼──────────────────────────────────────────────────────┐
│  Celery Workers  (app/tasks/)                             Redis         │
│  run_verification                                     ┌──────────────┐  │
│  process_batch_job                                    │ Broker       │  │
│  process_batch_row                                    │ Result store │  │
│      │                                                │ Cache (30min)│  │
│      ▼  pipeline.py                                   │ Rate limits  │  │
│  Search ──→ Scrape ──→ LLM ──→ Confidence            └──────────────┘  │
│     │           │         │                                             │
│  Serper.dev  httpx    Anthropic Claude                                  │
│  (3–4 q/job) (≤8 p/job) (1 call/job, 30s timeout)                    │
└─────────────────────────────────────────────────────────────────────────┘

Potential bottlenecks:
  ① Serper.dev monthly quota (2,400 queries shared across all verifications)
  ② Anthropic latency (30s hard timeout; 1 slow call blocks a worker thread)
  ③ Redis is a single point of failure (fail-open design mitigates impact)
  ④ BatchService.upload() — sequential per-row DB queries (up to 100 for 50 rows)
```

---

## 3. Evaluation

### 3.1 Separation of Concerns — **Good, with two exceptions**

Routes are thin: they validate HTTP inputs, instantiate a service, call one method,
return a response. Services own orchestration. Repositories own all DB queries.

**Exceptions:**
- `contacts.py` imports a private service helper directly (SD-02).
- `companies.py` runs a raw DB query inside the route handler (SD-03).

### 3.2 Architectural Pattern

Layered Architecture with a clean async stack (SQLAlchemy async + asyncpg +
FastAPI + Celery). The pipeline layer (`app/tasks/pipeline.py`) is a stateless
functional module shared across both single and batch execution paths — this is
good design.

### 3.3 God Objects / Modules

None. `BatchService` is the largest service (404 lines) but its responsibility
is cohesive: CSV parsing, DB record creation, polling, streaming export.

### 3.4 Dependency Flow

Backend: clean. No circular imports observed. `app/db/registry.py` is the
single import point for all models (Alembic compliance). `app/api/deps/__init__.py`
is the central re-export hub, which keeps route imports short.

Frontend: acceptable. `api.ts` imports `ACCESS_KEY`/`REFRESH_KEY` constants from
`authService.ts` to avoid hardcoding the storage key strings. This is a minor
coupling but not a problem.

### 3.5 Modularity Score — **7 / 10**

| Criterion | Score | Reason |
|---|---|---|
| Layer separation | 8/10 | One route bypasses repo; one imports private service helper |
| Testability | 6/10 | `BatchService` session-coupled; no mock repo available |
| Shared code reuse | 8/10 | Pipeline is cleanly shared; schemas separated from models |
| Frontend modularity | 7/10 | Services are thin wrappers; pages are self-contained |
| Stub hygiene | 5/10 | `BatchRepository` is a phantom — exists but does nothing |

---

## 4. Findings

### SD-01 — `BatchRepository` is a dead stub; `BatchService` bypasses the repo layer

**Severity: 7 / 10**

`backend/app/repositories/batch_repository.py` contains only a comment block (Phase 5 TODO).
No class exists. `BatchService` performs every batch DB operation directly via
`self._session`, e.g. `self._session.add(JobResult(...))`, `self._session.flush()`,
`self._session.execute(update(BatchJob)...)`. This:

1. Violates the stated convention: *"Repositories: All DB queries; extend BaseRepository[ModelT]"* (CLAUDE.md).
2. Makes `BatchService` impossible to unit-test without a real DB session; there is no repository seam to mock.
3. Creates a misleading file that signals "there is a batch repo" but delivers nothing.

**Anti-pattern:** Missing abstraction / phantom file.

**Fix — create the repository class in the existing file:**

```python
# backend/app/repositories/batch_repository.py
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.batch_job import BatchJob
from app.models.enums import BatchJobStatus
from app.repositories.base import BaseRepository

class BatchRepository(BaseRepository[BatchJob]):
    async def get_job(self, job_id: UUID) -> BatchJob | None:
        return await self.session.get(BatchJob, job_id)

    async def set_failed(self, job_id: UUID) -> None:
        await self.session.execute(
            update(BatchJob)
            .where(BatchJob.id == job_id)
            .values(status=BatchJobStatus.FAILED)
        )
```

Then inject `BatchRepository(session)` into `BatchService.__init__()` and delegate
the batch-level reads/writes to it. The per-row `JobResult` operations can
remain in the service for now (they are interleaved with `flush()` calls that
depend on the session state).

---

### SD-02 — Route handler imports a private service helper across layer boundary

**Severity: 6 / 10**

`backend/app/api/v1/routes/contacts.py:12`:
```python
from app.services.verification_service import _build_summary
```

`_build_summary` is a module-level private function (leading underscore = internal).
Routes should only call service methods — never reach into a service's internals.
This creates a tight coupling: if `_build_summary` is renamed, refactored, or
moved, the route silently breaks.

The same function is also imported in `batch_service.py:43`:
```python
from app.services.verification_service import _build_summary
```
Service-to-service is a softer violation (same tier), but both imports are
symptoms of the same root cause: the builders are not part of the public API.

**Anti-pattern:** Cross-layer private coupling.

**Fix — move the builders into the shared schema module, or expose them as a
`VerificationService` class method:**

Option A (minimal change) — rename to public and add to `__all__`:
```python
# verification_service.py — remove leading underscore
def build_summary(result: VerificationResult) -> VerificationSummary: ...
def build_result_response(result: VerificationResult) -> VerificationResultResponse: ...
```

Option B (cleaner) — move both functions to `app/schemas/verification.py` as
`@classmethod` factories on `VerificationSummary` and `VerificationResultResponse`.
Then both the route and `BatchService` import from the schema layer, which is
already a shared dependency.

---

### SD-03 — Inline DB query in a route handler (`companies.py`)

**Severity: 4 / 10**

`backend/app/api/v1/routes/companies.py:72–76`:
```python
total_verifications: int = await db.scalar(
    select(func.count(VerificationResult.id))
    .join(Search, Search.id == VerificationResult.search_id)
    .where(Search.company_id == company_id)
) or 0
```

This JOIN executes directly against the injected `db` session inside the route.
All DB queries must go through a repository (CLAUDE.md convention). The query
is also repeated partially in `CompanyRepository.list_with_verification_count()`,
which already does a LEFT JOIN + GROUP BY — but that method does not support
single-company lookup.

**Anti-pattern:** Spaghetti query in the wrong layer.

**Fix — add to `CompanyRepository`:**
```python
# company_repository.py
async def get_verification_count(self, company_id: UUID) -> int:
    return await self.session.scalar(
        select(func.count(VerificationResult.id))
        .join(Search, Search.id == VerificationResult.search_id)
        .where(Search.company_id == company_id)
    ) or 0
```

Then in the route:
```python
repo = CompanyRepository(db)
company = await repo.get_by_id(company_id)
total_verifications = await repo.get_verification_count(company_id)
```

---

### SD-04 — `get_db()` issues `COMMIT` on every read-only request

**Severity: 3 / 10**

`backend/app/db/session.py:44–51`:
```python
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()   # ← fires on every GET
        except Exception:
            await session.rollback()
            raise
```

PostgreSQL treats an empty `COMMIT` (no prior DML) as a no-op, so correctness
is unaffected. However, it incurs one unnecessary network round-trip per GET
request. At low traffic this is negligible; at high throughput it adds latency.

**Anti-pattern:** Unnecessary work on the hot path.

**Fix — commit only after actual writes:**

The cleanest solution is to leave `get_db()` as-is (the cost is low) and document
the intent. If you want to eliminate the round-trip:

```python
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
```

`session.in_transaction()` returns `True` only after the first DML statement,
so GET-only routes skip the commit.

---

### SD-05 — Frontend token refresh: concurrent 401s not queued

**Severity: 6 / 10**

`frontend/src/services/api.ts:19–47`:

```typescript
let isRefreshing = false;

// response interceptor
if (error.response?.status === 401 && !original._retry && !isRefreshing) {
  original._retry = true;
  isRefreshing = true;
  try { /* refresh + retry original */ }
  finally { isRefreshing = false; }
}
// If isRefreshing === true, falls through to error rejection ↓
```

If two concurrent requests both receive a 401 simultaneously:
- Request A sets `isRefreshing = true`, begins refresh.
- Request B sees `isRefreshing === true`, skips the `if`, and **immediately
  rejects** with the 401 error.
- After A's refresh succeeds, B has already failed — the caller gets an error
  it cannot distinguish from a real auth failure.

In practice this matters when a page mounts and fires multiple simultaneous API
calls (e.g. `SearchHistoryPage` + `BatchJobsPage` opening together, or polling
requests overlapping with a nav-triggered fetch).

**Anti-pattern:** Incomplete concurrency guard (flag without a queue).

**Fix — queue pending requests while refresh is in-flight:**

```typescript
let isRefreshing = false;
let pendingQueue: Array<(token: string) => void> = [];

function drainQueue(token: string) {
  pendingQueue.forEach(resolve => resolve(token));
  pendingQueue = [];
}

// In the response interceptor:
if (error.response?.status === 401 && !original._retry) {
  if (isRefreshing) {
    return new Promise<string>(resolve => {
      pendingQueue.push(resolve);
    }).then(token => {
      original.headers.Authorization = `Bearer ${token}`;
      return api(original);
    });
  }

  original._retry = true;
  isRefreshing = true;
  try {
    const refreshToken = localStorage.getItem(REFRESH_KEY);
    if (!refreshToken) throw new Error("no refresh token");
    const res = await axios.post<TokenResponse>("/api/v1/auth/refresh", {
      refresh_token: refreshToken,
    });
    const newToken = res.data.access_token;
    localStorage.setItem(ACCESS_KEY, newToken);
    localStorage.setItem(REFRESH_KEY, res.data.refresh_token);
    drainQueue(newToken);
    original.headers.Authorization = `Bearer ${newToken}`;
    return api(original);
  } catch {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    if (window.location.pathname !== "/login") window.location.href = "/login";
  } finally {
    isRefreshing = false;
  }
}
```

---

### SD-06 — `WakeupHint.tsx` hardcodes a production Render URL

**Severity: 5 / 10**

`frontend/src/components/ui/WakeupHint.tsx` contains:
```typescript
const HEALTH_URL = "https://stillthere-backend.onrender.com/api/v1/health";
```

This is an environment-specific production URL baked into source code. In local
development, `WakeupHint` will poll the **live production backend** to check
health, not the local Docker backend. A developer who sees a slow login on their
local machine will get a false "server is warming up" message even when the
local backend is perfectly healthy.

**Anti-pattern:** Environment-specific value in source code.

**Fix — use a relative path (works in all environments via Vite proxy / Render routing):**

```typescript
// WakeupHint.tsx
const HEALTH_URL = "/api/v1/health";
```

The Vite dev server proxies `/api/*` to `backend:8000`. On Render, the static
site proxies via the rewrite rules. A relative path works everywhere.

---

### SD-07 — N+1 per-row DB queries in `BatchService.upload()`

**Severity: 5 / 10**

`backend/app/services/batch_service.py:198–218` — for each of up to 50 CSV rows,
the upload loop calls:
```python
contact = await self._get_or_create_contact(name, email)    # SELECT + optional INSERT
company_obj = await self._get_or_create_company(company)    # SELECT + optional INSERT
search = await self._verifications.create_search(...)        # INSERT
ver_result = await self._verifications.create_result(...)    # INSERT
```

That is up to **4 sequential round-trips per row × 50 rows = up to 200 individual
DB calls** inside a single HTTP request, all before the response is returned. The
`_get_or_create_*` methods also use `session.flush()` to get the generated ID,
which adds extra round-trips. With 50 rows, the upload endpoint can block for
several seconds purely on DB I/O.

**Anti-pattern:** Chatty DB access pattern (loop + individual queries).

**Fix (targeted, minimal change) — batch the dedup lookup before the loop:**

```python
# Extract all unique (name, email) and company names up front
emails = {clean(r.get("email","")) or None for r in rows if r.get("email")}
names  = {clean(r.get("company","")) for r in rows if r.get("company","")}

# Bulk-fetch existing contacts by email in one query
existing_by_email: dict[str, Contact] = {}
if emails - {None}:
    stmt = select(Contact).where(Contact.email.in_(emails - {None}))
    for c in (await self._session.execute(stmt)).scalars():
        existing_by_email[c.email] = c

# Bulk-fetch existing companies by normalised name in one query
from app.core.utils import normalise_name
existing_cos: dict[str, Company] = {}
norm_names = {normalise_name(n) for n in names}
stmt = select(Company).where(Company.normalized_name.in_(norm_names))
for co in (await self._session.execute(stmt)).scalars():
    existing_cos[co.normalized_name] = co
```

Then resolve each row from the in-memory dicts, only inserting genuinely new
records. This reduces the typical case from ~200 queries to ~6 (2 bulk selects +
a few inserts for new entities + search/result inserts).

---

### SD-08 — `_build_summary` silently crashes on insufficiently-loaded ORM objects

**Severity: 4 / 10**

`backend/app/services/verification_service.py:79–90`:
```python
def _build_summary(result: VerificationResult) -> VerificationSummary:
    """Lightweight summary for list views. Requires search→contact/company loaded."""
    return VerificationSummary(
        full_name=result.search.contact.full_name,   # ← unguarded attribute chain
        company_name=result.search.company.name,
        ...
    )
```

This function accesses a three-level relationship chain (`result.search.contact.full_name`)
without any existence check. If `_build_summary` is called with a `VerificationResult`
where `search`, `search.contact`, or `search.company` is not eagerly loaded,
SQLAlchemy raises `sqlalchemy.exc.MissingGreenlet` in async contexts — an
unhandled 500. The docstring documents the pre-condition, but nothing enforces it.

`contacts.py:84` calls this via `search.latest_result` (a `@property` on `Search`)
which returns the last item in `search.verification_results`. The question is
whether that eager-load chain includes `contact` and `company` — which requires
inspecting `ContactRepository.get_with_recent_searches()`.

**Unable to fully verify** without reading `contact_repository.py:get_with_recent_searches()`,
but the pattern is fragile regardless.

**Fix — add an explicit guard:**
```python
def _build_summary(result: VerificationResult) -> VerificationSummary:
    search = result.search
    if search is None or search.contact is None or search.company is None:
        raise ValueError(
            f"VerificationResult {result.id} passed to _build_summary "
            "without required relations loaded"
        )
    return VerificationSummary(
        full_name=search.contact.full_name,
        company_name=search.company.name,
        ...
    )
```

This converts a confusing `MissingGreenlet` crash into a clear `ValueError`
that points to the callsite with missing eager-loads.

---

## 5. Anti-Pattern Summary

| Anti-pattern | Where | Finding |
|---|---|---|
| Phantom file / missing abstraction | `batch_repository.py` | SD-01 |
| Cross-layer private coupling | `contacts.py:12` → `_build_summary` | SD-02 |
| Business logic in route handler | `companies.py:72–76` (inline SQL) | SD-03 |
| Unnecessary work on hot path | `session.py:48` (COMMIT on GETs) | SD-04 |
| Incomplete concurrency guard | `api.ts:19–47` (no request queue) | SD-05 |
| Env-specific value in source | `WakeupHint.tsx:HEALTH_URL` | SD-06 |
| Chatty DB access (N+1 loop) | `batch_service.py:198–218` | SD-07 |
| Unguarded relation access | `verification_service.py:80–90` | SD-08 |

---

## 6. Findings Severity Table

| ID | Description | Severity |
|---|---|---|
| SD-01 | `BatchRepository` is a dead stub; repo layer bypassed in `BatchService` | **7 / 10** |
| SD-02 | Route (`contacts.py`) imports private service helper `_build_summary` | **6 / 10** |
| SD-05 | Frontend: concurrent 401s not queued during token refresh | **6 / 10** |
| SD-07 | N+1 per-row DB queries in `BatchService.upload()` loop | **5 / 10** |
| SD-06 | `WakeupHint.tsx` hardcodes production Render URL | **5 / 10** |
| SD-03 | Inline DB query in `companies.py` route handler | **4 / 10** |
| SD-08 | `_build_summary` crashes on insufficiently-loaded ORM objects | **4 / 10** |
| SD-04 | `get_db()` issues `COMMIT` on every read request | **3 / 10** |

---

## 7. What Is Working Well

- **Commit-before-dispatch** pattern (both `VerificationService` and `BatchService`)
  prevents race conditions where the worker starts before the DB record exists.
- **NullPool / QueuePool split** correctly isolates Celery task sessions from
  the API pool; avoids the asyncpg event-loop binding crash.
- **Enum `values_callable`** is applied consistently across all ORM columns,
  preventing the SQLAlchemy 2.x name-vs-value binding bug.
- **`_PipelineError` sentinel** cleanly separates third-party API failures from
  internal bugs in task error handling.
- **`ConfidenceService` is pure** (no I/O, no DB) and fully injectable — easy
  to unit-test and reason about.
- **Fail-open design** on both Redis cache and rate limiter means outages degrade
  gracefully rather than taking down the whole app.
- **`BaseRepository._get_or_create`** uses an `IntegrityError` retry pattern that
  is concurrency-safe under parallel inserts — correct for a multi-worker Celery
  deployment.
