# Design Pattern Audit

**Project:** StillThere — contact-verification-platform  
**Date:** 2026-07-06  
**Scope:** Full backend (FastAPI / Python 3.11) + frontend (React 18 / TypeScript)

---

## Executive Summary

Most patterns are applied correctly. Repository, Facade, Proxy, Strategy, and Command patterns all land in the right places. Ten findings are listed below, ordered by importance. The single most significant issue is that the auth route is the sole exception to the project's own "routes are HTTP-only" rule — it contains token creation, password hashing, and refresh-token rotation inline, with no `AuthService` to unit-test against.

---

## Pattern Inventory

### Creational

| Pattern | Location | Verdict |
|---|---|---|
| Singleton (`get_settings`) | `core/config.py:86` — `@lru_cache()` | ✅ Correct and idiomatic |
| Singleton (DB engines) | `db/session.py:8–31` — module-level | ✅ Correct — engines are designed to be process-level singletons |
| Factory (`_get_or_create`) | `repositories/base.py:46–60` | ✅ Correct — race-safe via `IntegrityError` retry |
| Factory (`PaginatedResponse.build`) | `schemas/common.py:19–33` | ✅ Hides page/offset/total_pages math from all callers |

### Structural

| Pattern | Location | Verdict |
|---|---|---|
| Facade (`VerificationService`) | `services/verification_service.py` | ✅ Routes call one method; service owns all orchestration |
| Facade (`BatchService`) | `services/batch_service.py` | ✅ Correct |
| Proxy (`CacheService`) | `services/cache_service.py` | ✅ Adds null-safety, key namespacing, JSON serialisation, TTL |
| Adapter (`SearchProvider` Protocol) | `services/search_service.py:32–47` | ✅ Structural protocol — Serper is swappable without subclassing |
| Adapter (axios `api.ts`) | `frontend/src/services/api.ts:4–33` | ⚠️ 401 handler duplicates key strings (P-04) |
| Decorator (ASGI middleware stack) | `main.py:50–58` | ✅ CORS + SlowAPI layered correctly |
| Decorator (`@limiter.limit`) | `routes/auth.py:38,53,86` | ✅ Per-route rate limiting |

### Behavioral

| Pattern | Location | Verdict |
|---|---|---|
| Strategy (`SearchProvider` injected) | `tasks/verification_tasks.py:86–96` | ✅ `execute_pipeline` accepts any provider |
| Command (Celery dispatch) | `tasks/*.py` — `.delay()` / `.apply_async()` | ✅ Serialisable command over Redis broker |
| State Machine (idempotency guards) | `verification_tasks.py:208–235`, `batch_tasks.py:155–194` | ✅ Correct PENDING→RUNNING→terminal flow |
| Template Method (`execute_pipeline`) | `tasks/verification_tasks.py:81–153` | ✅ Fixed 4-stage pipeline; stages are swappable via injection |
| Observer (TanStack Query polling) | Frontend pages | ✅ `refetchInterval` adapts to `status` field |
| Chain of Responsibility (deps) | `api/deps/auth.py` | ✅ `get_current_admin` → `get_current_user` → `_bearer` |

### Domain

| Pattern | Location | Verdict |
|---|---|---|
| Repository (`BaseRepository[ModelT]`) | `repositories/base.py` + concrete repos | ✅ Generic base, domain queries in subclasses |
| Service Layer | `services/` | ⚠️ Auth route bypasses it (P-01) |
| DTO / Value Objects | `schemas/`, `@dataclass` in tasks, `PageContent`, `SearchHit` | ⚠️ `LLMEvidenceSource` leaks across layers (P-03) |
| Rich Domain Model | `models/` — `@validates`, `@property` | ✅ Appropriate — only invariants on the model itself |
| Unit of Work | `db/session.py:40–51` `get_db()` | ✅ Per-request transaction with auto commit/rollback |

---

## Findings

---

### P-01 — No `AuthService`: auth route contains inline business logic
**Importance: 9/10**

**Location:** `backend/app/api/v1/routes/auth.py:37–121`

`/register` and `/login` call `hash_password()`, `create_access_token()`, `generate_refresh_token()`, `refresh_token_expires_at()`, and `verify_password()` directly inside route functions. `UserRepository` and `RefreshTokenRepository` are also instantiated there. This is the only route module that bypasses the service layer — every other route (`verifications.py`, `batch.py`, `admin.py`) delegates entirely to a service class.

**Why it matters:** CLAUDE.md states "Routes: HTTP only — no business logic." Auth is the most security-critical path (token rotation, constant-time password comparison, user enumeration protection), yet it cannot be unit-tested without going through HTTP. Adding a new OAuth provider or changing token format requires editing a route file.

**Remedy:** Create `backend/app/services/auth_service.py`:

```python
from app.core.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, generate_refresh_token,
    hash_password, hash_token, refresh_token_expires_at, verify_password,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import TokenResponse


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    async def register(self, email: str, full_name: str, password: str) -> User:
        if await self._users.email_exists(email):
            raise ValueError("email_exists")
        return await self._users.create(email, full_name, hash_password(password))

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self._users.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise ValueError("invalid_credentials")
        if not user.is_active:
            raise ValueError("inactive")
        access = create_access_token(str(user.id))
        raw, hashed = generate_refresh_token()
        await self._tokens.create(user.id, hashed, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def refresh(self, raw_token: str) -> TokenResponse:
        stored = await self._tokens.get_valid_by_hash(hash_token(raw_token))
        if not stored:
            raise ValueError("invalid_token")
        await self._tokens.revoke(stored.token_hash)
        access = create_access_token(str(stored.user_id))
        raw, new_hash = generate_refresh_token()
        await self._tokens.create(stored.user_id, new_hash, refresh_token_expires_at())
        return TokenResponse(
            access_token=access,
            refresh_token=raw,
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def logout(self, raw_token: str) -> None:
        await self._tokens.revoke(hash_token(raw_token))
```

Routes become thin HTTP wrappers that catch `ValueError` and map to the appropriate `HTTPException`.

---

### P-02 — `run_pipeline` / `_apply_pipeline_result` cross-imported between task modules
**Importance: 7/10**

**Location:** `backend/app/tasks/batch_tasks.py:58`

```python
from app.tasks.verification_tasks import _apply_pipeline_result, run_pipeline
```

`_apply_pipeline_result` is an underscore-prefixed internal function in `verification_tasks.py`. `run_pipeline` also lives there. Both are re-used by the batch path, creating inter-module coupling: refactoring either task file can silently break the other, and circular imports become a risk.

**Remedy:** Extract shared pipeline code to `backend/app/tasks/pipeline.py`:

```
backend/app/tasks/
  pipeline.py          ← execute_pipeline, run_pipeline, _apply_pipeline_result,
                          PipelineResult, _PipelineError, EvidenceData (see P-03)
  verification_tasks.py ← _check_and_set_running, _run_verification_async, run_verification
  batch_tasks.py        ← _process_batch_job_async, _process_batch_row_async,
                          process_batch_job, process_batch_row, _increment_counters
```

Both task files then import from `pipeline.py`. No task file imports another.

---

### P-03 — `LLMEvidenceSource` (LLM-internal Pydantic type) leaks into the task layer
**Importance: 5/10**

**Location:** `backend/app/tasks/verification_tasks.py:52,74`

```python
from app.services.llm_service import LLMEvidenceSource, LLMService
...
@dataclass
class PipelineResult:
    evidence_sources: list[LLMEvidenceSource]   # LLM-service-internal type
```

`PipelineResult` is the task layer's data contract, consumed by `_apply_pipeline_result` which writes rows to the DB. It should not depend on a Pydantic model from the LLM service's internal schema. If `LLMService` ever changes its output model, `PipelineResult` breaks.

**Remedy:** Define a neutral `EvidenceData` dataclass (best placed in the new `pipeline.py` from P-02):

```python
@dataclass
class EvidenceData:
    url: str
    title: str
    source_type: EvidenceSourceType
    explanation: str
```

In `execute_pipeline`, translate immediately after `llm_service.analyse()`:

```python
evidence_sources=[
    EvidenceData(
        url=src.url,
        title=src.title,
        source_type=src.source_type,
        explanation=src.explanation,
    )
    for src in analysis.evidence_sources
],
```

`LLMEvidenceSource` then stays entirely inside `llm_service.py`.

---

### P-04 — `api.ts` 401 interceptor duplicates `authService`'s localStorage key strings
**Importance: 5/10**

**Location:** `frontend/src/services/api.ts:20–23`

```ts
localStorage.removeItem("stillthere_access_token");
localStorage.removeItem("stillthere_refresh_token");
```

`authService.ts` already owns the canonical key names via `ACCESS_KEY` and `REFRESH_KEY` constants, and exposes `clearTokens()`. The 401 interceptor bypasses it and writes to localStorage directly, duplicating string literals. If key names change in `authService`, the interceptor silently stops clearing the right keys.

**Remedy:**

```ts
// api.ts — add import
import { authService } from "@/services/authService";

// replace the two localStorage.removeItem calls with:
authService.clearTokens();
```

---

### P-05 — `BatchService.export_csv_stream()` hard-codes `AsyncSessionLocal`, bypassing DI
**Importance: 4/10**

**Location:** `backend/app/services/batch_service.py:327,353`

```python
@staticmethod
async def export_csv_stream(job_id: UUID) -> AsyncGenerator[bytes, None]:
    ...
    async with AsyncSessionLocal() as session:   # hardcoded; no injection seam
```

The method is `@staticmethod` (it has no `self` to carry an injected session) and opens a bare `AsyncSessionLocal`. This is intentional — streaming continues after the route handler's injected session closes. But the hardcoding makes it impossible to pass a test session without patching the module.

**Remedy:** Accept an optional `session_factory` parameter:

```python
@staticmethod
async def export_csv_stream(
    job_id: UUID,
    session_factory=None,
) -> AsyncGenerator[bytes, None]:
    factory = session_factory or AsyncSessionLocal
    async with factory() as session:
        ...
```

Tests pass a `contextlib.asynccontextmanager`-wrapped fake session; production calls omit the parameter.

---

### P-06 — `BaseRepository.get_all()` provides no ordering guarantee
**Importance: 3/10**

**Location:** `backend/app/repositories/base.py:27–30`

```python
async def get_all(self, offset: int = 0, limit: int = 20) -> list[ModelT]:
    result = await self.session.execute(select(self.model).offset(offset).limit(limit))
    return list(result.scalars().all())
```

PostgreSQL does not guarantee row ordering without `ORDER BY`. In practice, every concrete repository that needs an ordered list writes its own query method (`list_with_relations`, `list_jobs`, etc.), making `get_all` unused for any real feature.

**Remedy:**

```python
async def get_all(
    self,
    offset: int = 0,
    limit: int = 20,
    order_by=None,
) -> list[ModelT]:
    stmt = select(self.model)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    result = await self.session.execute(stmt.offset(offset).limit(limit))
    return list(result.scalars().all())
```

---

### P-07 — `SearchService._build_queries()` has no seam for customization
**Importance: 3/10**

**Location:** `backend/app/services/search_service.py:147–161`

Query templates are hardcoded in a `@staticmethod`. The `SearchProvider` Protocol correctly abstracts the provider, but within `SearchService` there is no injection point for different query strategies (e.g., different industry verticals, international searches, or executive-level lookups).

**Verdict:** Not worth changing now — premature abstraction. If a second query profile is ever needed, accept a `query_builder: Callable[[str, str, str | None], list[tuple[str, str]]]` in `SearchService.__init__` defaulting to `_build_queries`. Leave as-is until there is a concrete second use case.

---

### P-08 — `ConfidenceService` scoring weights are not injectable
**Importance: 2/10**

**Location:** `backend/app/services/confidence_service.py:28–40`

`_SOURCE_WEIGHTS` and `_LEVEL_THRESHOLDS` are private module-level constants. Fine for a single scoring profile. If different weight profiles are ever needed (per industry, per customer tier), there is no injection point.

**Remedy (low priority):**

```python
class ConfidenceService:
    def __init__(
        self,
        source_weights: dict[EvidenceSourceType, int] | None = None,
        level_thresholds: list[tuple[int, ConfidenceLevel]] | None = None,
    ) -> None:
        self._weights = source_weights or _SOURCE_WEIGHTS
        self._thresholds = level_thresholds or _LEVEL_THRESHOLDS

    @staticmethod
    def _field_score(tri_states: dict[str, TriState]) -> int:
        determined = sum(1 for v in tri_states.values() if v != TriState.UNCLEAR)
        return determined * 10

    def _source_score(self, source_types: list[EvidenceSourceType]) -> int:
        return min(50, sum(self._weights.get(t, 3) for t in source_types))
```

---

### P-09 — `CacheService` key templates use positional `{}` rather than named `{name}`
**Importance: 2/10**

**Location:** `backend/app/services/cache_service.py:33–35`

```python
_KEY_COMPANY_PROFILE = "stillthere:company:{}:profile"
_KEY_COMPANY_ACTIVE  = "stillthere:company:{}:active"
_KEY_SEARCH_RESULTS  = "stillthere:search:{}:results"
```

Positional format strings raise `IndexError` silently for wrong-arity calls. Named placeholders raise `KeyError` with the missing argument name, giving a clear diagnosis.

**Remedy:** Switch to named placeholders:

```python
_KEY_COMPANY_PROFILE = "stillthere:company:{name}:profile"
_KEY_COMPANY_ACTIVE  = "stillthere:company:{name}:active"
_KEY_SEARCH_RESULTS  = "stillthere:search:{query_hash}:results"

# usage (e.g. get_company_profile):
await self.get(self._KEY_COMPANY_PROFILE.format(name=normalized_name))
```

---

### P-10 — `api.ts` 401 handler navigates via `window.location.href`, bypassing React Router
**Importance: 2/10**

**Location:** `frontend/src/services/api.ts:23`

```ts
window.location.href = "/login";
```

This causes a full page reload instead of an SPA navigation, dropping React query cache and component state. The current behaviour is tolerable (login redirect is rare in normal usage), but it breaks the Observer/Router pattern React Router establishes.

An axios interceptor cannot safely call React Router's `navigate()` directly because it runs outside the component tree. The idiomatic fix is a custom DOM event:

```ts
// api.ts — replace window.location.href line with:
window.dispatchEvent(new CustomEvent("auth:unauthorized"));

// AuthContext.tsx — inside AuthProvider, add to the mount useEffect:
const navigate = useNavigate();
useEffect(() => {
  const handler = () => navigate("/login", { replace: true });
  window.addEventListener("auth:unauthorized", handler);
  return () => window.removeEventListener("auth:unauthorized", handler);
}, [navigate]);
```

Apply only if the UX degradation (full reload on session expiry) is reported as noticeable.

---

## Finding Priority Matrix

| ID | Finding | Importance | Effort |
|---|---|---|---|
| P-01 | No `AuthService` — business logic in auth route | 9/10 | Medium |
| P-02 | `run_pipeline` cross-imported between task modules | 7/10 | Low |
| P-03 | `LLMEvidenceSource` leaks into task layer | 5/10 | Low |
| P-04 | `api.ts` 401 handler bypasses `authService.clearTokens()` | 5/10 | Trivial |
| P-05 | `export_csv_stream` hard-codes `AsyncSessionLocal` | 4/10 | Low |
| P-06 | `BaseRepository.get_all()` has no ordering | 3/10 | Trivial |
| P-07 | `SearchService._build_queries` has no customization seam | 3/10 | — (defer) |
| P-08 | `ConfidenceService` weights not injectable | 2/10 | Low |
| P-09 | `CacheService` key templates use positional `{}` | 2/10 | Trivial |
| P-10 | `api.ts` navigates via `window.location.href` | 2/10 | Low |

**Recommended order:** P-04 (one-liner) → P-09 (six format calls) → P-02 + P-03 together (extract `pipeline.py`) → P-01 (new file, highest payoff) → P-05, P-06, P-08 if time allows.
