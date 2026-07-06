# Code Duplication Audit — StillThere

**Date:** 2026-07-06  
**Scope:** Full codebase — `backend/app/`, `backend/tests/`, `frontend/src/`

---

## Summary

| ID | Category | Location | Importance | Effort |
|----|----------|----------|------------|--------|
| [F-01](#f-01-exact--pipeline-service-instantiation) | Exact | `verification_tasks.py` / `batch_tasks.py` | **8/10** | Low |
| [F-02](#f-02-exact--verificationresult-field-write-block) | Exact | `verification_tasks.py` / `batch_tasks.py` | **7/10** | Low |
| [F-03](#f-03-exact--error-message-truncation-expression) | Exact | `verification_tasks.py` / `batch_tasks.py` | **5/10** | Trivial |
| [F-04](#f-04-near--_normalise-string-in-three-places) | Near | Models + `company_repository.py` | **6/10** | Low |
| [F-05](#f-05-near--get_or_create-integrityerror-pattern) | Near | `contact_repository.py` / `company_repository.py` | **6/10** | Medium |
| [F-06](#f-06-near--_build_summary-construction) | Near | `verification_service.py` / `batch_service.py` | **5/10** | Low |
| [F-07](#f-07-structural--paginatedresponse-construction) | Structural | Three service methods | **5/10** | Low |
| [F-08](#f-08-structural--list_with_verification_count-query) | Structural | `contact_repository.py` / `company_repository.py` | **4/10** | Medium |
| [F-09](#f-09-structural--count-method-belongs-in-baserepository) | Structural | `contact_repository.py` / `company_repository.py` | **3/10** | Trivial |
| [F-10](#f-10-structural--frontend-loading--error--empty-state-triple) | Structural | Three page components | **4/10** | Medium |
| [F-11](#f-11-structural--protectedroute--adminroute-spinner-jsx) | Structural | `ProtectedRoute.tsx` / `AdminRoute.tsx` | **3/10** | Trivial |
| [F-12](#f-12-structural--paginated-get-service-methods) | Structural | Three frontend services | **3/10** | Low |
| [F-13](#f-13-data--mock-llm-client-in-two-test-files) | Data | `test_verification_pipeline.py` / `test_batch_pipeline.py` | **4/10** | Low |

Duplication percentage estimates are relative to the duplicated block, not the whole file.

---

## F-01 — EXACT — Pipeline service instantiation

**Importance: 8/10**

**Files:**
- `backend/app/tasks/verification_tasks.py` lines 221–234
- `backend/app/tasks/batch_tasks.py` lines 211–224

**Duplication: ~100% (13 lines)**

Identical `httpx.AsyncClient` context manager with all four service constructors is copy-pasted verbatim between both task files:

```python
# verification_tasks.py lines 222–234  (and batch_tasks.py lines 212–224 — identical)
async with httpx.AsyncClient() as http_client:
    pipeline_result = await execute_pipeline(
        name=name,
        company=company,
        email=email,
        search_service=SearchService(
            api_key=settings.SERPER_API_KEY,
            http_client=http_client,
        ),
        evidence_service=EvidenceService(http_client=http_client),
        llm_service=LLMService(api_key=settings.ANTHROPIC_API_KEY),
        confidence_service=ConfidenceService(),
    )
```

Any addition of a service constructor argument (e.g. a timeout, a cache layer) requires the same edit in two files.

**Fix:** Extract a helper into `verification_tasks.py` (where `execute_pipeline` already lives) and import it in `batch_tasks.py`:

```python
# verification_tasks.py — add below execute_pipeline
async def run_pipeline(name: str, company: str, email: str | None) -> PipelineResult:
    settings = get_settings()
    async with httpx.AsyncClient() as http_client:
        return await execute_pipeline(
            name=name,
            company=company,
            email=email,
            search_service=SearchService(
                api_key=settings.SERPER_API_KEY,
                http_client=http_client,
            ),
            evidence_service=EvidenceService(http_client=http_client),
            llm_service=LLMService(api_key=settings.ANTHROPIC_API_KEY),
            confidence_service=ConfidenceService(),
        )
```

Both task orchestrators then collapse to `pipeline_result = await run_pipeline(name, company, email)`.

---

## F-02 — EXACT — `VerificationResult` field write block

**Importance: 7/10**

**Files:**
- `backend/app/tasks/verification_tasks.py` lines 254–275 (inside `_run_verification_async`)
- `backend/app/tasks/batch_tasks.py` lines 250–269 (inside `_process_batch_row_async`)

**Duplication: ~90% (21 lines)**

Both write the same nine fields onto a `VerificationResult` ORM object and then loop to create `EvidenceSource` rows:

```python
# Both files — identical block
result.status = VerificationStatus.COMPLETE
result.person_found = pipeline_result.person_found
result.appears_associated = pipeline_result.appears_associated
result.found_on_website = pipeline_result.found_on_website
result.company_active = pipeline_result.company_active
result.email_match = pipeline_result.email_match
result.confidence_score = pipeline_result.confidence_score
result.confidence_level = pipeline_result.confidence_level
result.useful_links = pipeline_result.useful_links
result.raw_search_data = pipeline_result.raw_search_data

for src in pipeline_result.evidence_sources:
    session.add(EvidenceSource(
        verification_result_id=result_uuid,
        url=src.url,
        title=src.title or None,
        snippet=None,
        explanation=src.explanation or None,
        source_type=src.source_type,
    ))
```

Adding any new field to `PipelineResult` (e.g. a raw LLM token count) requires updating both files.

**Fix:** A module-level helper in `verification_tasks.py`:

```python
def _apply_pipeline_result(
    result: VerificationResult,
    pipeline: PipelineResult,
    session: AsyncSession,
    result_uuid: UUID,
) -> None:
    result.status = VerificationStatus.COMPLETE
    result.person_found = pipeline.person_found
    result.appears_associated = pipeline.appears_associated
    result.found_on_website = pipeline.found_on_website
    result.company_active = pipeline.company_active
    result.email_match = pipeline.email_match
    result.confidence_score = pipeline.confidence_score
    result.confidence_level = pipeline.confidence_level
    result.useful_links = pipeline.useful_links
    result.raw_search_data = pipeline.raw_search_data
    for src in pipeline.evidence_sources:
        session.add(EvidenceSource(
            verification_result_id=result_uuid,
            url=src.url,
            title=src.title or None,
            snippet=None,
            explanation=src.explanation or None,
            source_type=src.source_type,
        ))
```

Both call sites reduce to `_apply_pipeline_result(result, pipeline_result, session, result_uuid)`. Import it in `batch_tasks.py` alongside `execute_pipeline`.

---

## F-03 — EXACT — Error message truncation expression

**Importance: 5/10**

**Files:**
- `backend/app/tasks/verification_tasks.py` line 236
- `backend/app/tasks/batch_tasks.py` line 226

**Duplication: 100% (1 line)**

```python
error_msg = f"{type(exc).__name__}: {exc}"[:500]
```

**Fix:** One-liner utility in `app/core/logging.py` or a new `app/core/utils.py`:

```python
def format_exc_message(exc: Exception, max_len: int = 500) -> str:
    return f"{type(exc).__name__}: {exc}"[:max_len]
```

---

## F-04 — NEAR — `_normalise` string in three places

**Importance: 6/10**

**Files:**
- `backend/app/repositories/company_repository.py` line 15 — standalone `_normalise()` function
- `backend/app/models/contact.py` — `@validates("full_name")` uses `re.sub(r"\s+", " ", value.strip().lower())`
- `backend/app/models/company.py` — `@validates("name")` uses the same expression inline

Three independent implementations of the same whitespace-collapse + lowercase rule. If the normalisation logic ever changes (e.g. Unicode NFKC normalisation is added), all three must be updated.

**Fix:** Move to `app/core/utils.py` and import everywhere:

```python
# app/core/utils.py
import re

def normalise_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
```

Replace all three sites:
- `company_repository.py` line 15: `from app.core.utils import normalise_name` (delete `_normalise`)
- `contact.py` validator: `return normalise_name(value)`
- `company.py` validator: `return normalise_name(value)`

---

## F-05 — NEAR — `get_or_create` IntegrityError pattern

**Importance: 6/10**

**Files:**
- `backend/app/repositories/contact_repository.py` lines 34–53 (`get_or_create_by_email`)
- `backend/app/repositories/company_repository.py` lines 32–48 (`get_or_create`)

**Duplication: ~70% (structure and error handling identical)**

Both use the same try/except/rollback/re-fetch pattern to handle concurrent creation races:

```python
# Both repositories — identical structure
existing = await self.get_by_<key>(key_value)
if existing:
    return existing, False
obj = Model(...)
try:
    return await self.save(obj), True
except IntegrityError:
    await self.session.rollback()
    existing = await self.get_by_<key>(key_value)
    return existing, False
```

**Fix:** Generic helper in `BaseRepository`:

```python
# base.py
from typing import Callable, Awaitable
from sqlalchemy.exc import IntegrityError

async def _get_or_create(
    self,
    fetch: Callable[[], Awaitable[ModelT | None]],
    build: Callable[[], ModelT],
) -> tuple[ModelT, bool]:
    existing = await fetch()
    if existing:
        return existing, False
    try:
        return await self.save(build()), True
    except IntegrityError:
        await self.session.rollback()
        existing = await fetch()
        return existing, False  # type: ignore[return-value]
```

Each repository then delegates:
```python
# ContactRepository
async def get_or_create_by_email(self, full_name: str, email: str) -> tuple[Contact, bool]:
    email_lower = email.lower().strip()
    return await self._get_or_create(
        fetch=lambda: self.get_by_email(email_lower),
        build=lambda: Contact(full_name=full_name, email=email_lower),
    )
```

---

## F-06 — NEAR — `_build_summary` construction

**Importance: 5/10**

**Files:**
- `backend/app/services/verification_service.py` lines 79–90 (`_build_summary`)
- `backend/app/services/batch_service.py` lines 97–116 (`_build_job_result_response` contains inline identical `VerificationSummary(...)` construction at lines 99–108)

**Duplication: ~100% of the `VerificationSummary(...)` constructor call**

```python
# verification_service.py _build_summary (lines 81–90)
return VerificationSummary(
    id=result.id,
    search_id=result.search_id,
    status=result.status,
    full_name=result.search.contact.full_name,
    company_name=result.search.company.name,
    confidence_score=result.confidence_score,
    confidence_level=result.confidence_level,
    created_at=result.created_at,
)

# batch_service.py inside _build_job_result_response (lines 99–108) — identical
verification = VerificationSummary(
    id=vr.id,
    search_id=vr.search_id,
    status=vr.status,
    full_name=vr.search.contact.full_name,
    company_name=vr.search.company.name,
    confidence_score=vr.confidence_score,
    confidence_level=vr.confidence_level,
    created_at=vr.created_at,
)
```

**Fix:** Move `_build_summary` to `verification_service.py` as a module-level function and import it in `batch_service.py`:

```python
# verification_service.py — promote to module-level (already there, just export it)
# batch_service.py
from app.services.verification_service import _build_summary  # or rename to build_verification_summary

# Replace the inline block in _build_job_result_response:
verification = _build_summary(vr) if vr is not None and vr.search is not None else None
```

---

## F-07 — STRUCTURAL — `PaginatedResponse` construction

**Importance: 5/10**

**Files:**
- `backend/app/services/verification_service.py` lines 186–193 (`list_results`)
- `backend/app/services/batch_service.py` lines 273–279 (`list_jobs`)
- `backend/app/services/batch_service.py` lines 302–309 (`get_job_results`)

**Duplication: ~100% of the formula (4 occurrences across the codebase)**

```python
# Repeated in all three methods (identical arithmetic):
PaginatedResponse(
    items=[...],
    total=total,
    page=(offset // limit) + 1 if limit else 1,
    page_size=limit,
    total_pages=math.ceil(total / limit) if total and limit else 0,
)
```

**Fix:** Classmethod on `PaginatedResponse` in `app/schemas/common.py`:

```python
# common.py
import math
from typing import TypeVar

DataT = TypeVar("DataT")

class PaginatedResponse(BaseModel, Generic[DataT]):
    items: list[DataT]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(cls, items: list[DataT], total: int, offset: int, limit: int) -> "PaginatedResponse[DataT]":
        return cls(
            items=items,
            total=total,
            page=(offset // limit) + 1 if limit else 1,
            page_size=limit,
            total_pages=math.ceil(total / limit) if total and limit else 0,
        )
```

All call sites reduce to `PaginatedResponse.build(items, total, offset, limit)`.

---

## F-08 — STRUCTURAL — `list_with_verification_count` query

**Importance: 4/10**

**Files:**
- `backend/app/repositories/contact_repository.py` lines 65–82
- `backend/app/repositories/company_repository.py` lines 70–83

**Duplication: ~80% (LEFT JOIN + GROUP BY pattern identical; join key and sort differ)**

```python
# contact_repository.py
select(Contact, func.count(VerificationResult.id))
  .outerjoin(Search, Search.contact_id == Contact.id)   # ← contact_id
  .outerjoin(VerificationResult, VerificationResult.search_id == Search.id)
  .group_by(Contact.id)
  .order_by(Contact.created_at.desc())                  # ← desc by date

# company_repository.py
select(Company, func.count(VerificationResult.id))
  .outerjoin(Search, Search.company_id == Company.id)   # ← company_id
  .outerjoin(VerificationResult, VerificationResult.search_id == Search.id)
  .group_by(Company.id)
  .order_by(Company.name.asc())                         # ← asc by name
```

The schema difference (different FK names, different sort columns) means a fully generic helper is awkward. Mark as **Unable to fully abstract** without generic SQLAlchemy expression support, but consider extracting the join spine as a documented pattern in a comment block.

**Recommended action:** Document it in a comment, keep as-is unless both repos grow more count methods.

---

## F-09 — STRUCTURAL — `count()` method belongs in `BaseRepository`

**Importance: 3/10**

**Files:**
- `backend/app/repositories/contact_repository.py` lines 109–111
- `backend/app/repositories/company_repository.py` lines 85–87

**Duplication: 100% (same two lines)**

```python
async def count(self) -> int:
    result = await self.session.execute(select(func.count(Model.id)))
    return result.scalar_one()
```

**Fix:** Add to `BaseRepository` in `base.py`:

```python
# base.py
async def count(self) -> int:
    result = await self.session.execute(select(func.count(self.model.id)))
    return result.scalar_one()
```

Delete the two concrete implementations.

---

## F-10 — STRUCTURAL — Frontend loading / error / empty-state triple

**Importance: 4/10**

**Files:**
- `frontend/src/pages/SearchHistoryPage.tsx` lines 38–58
- `frontend/src/pages/BatchJobsPage.tsx` lines 78–98
- `frontend/src/pages/AdminPage.tsx` lines 31–48

**Duplication: ~80% (identical JSX structure, different strings)**

All three render:
```tsx
{isLoading && (
  <div className="flex flex-col items-center py-20">
    <Spinner size="lg" />
    <WakeupHint />
  </div>
)}

{error && (
  <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
    {error instanceof Error ? error.message : "Fallback string."}
  </div>
)}

{data && data.items.length === 0 && (
  <div className="rounded-xl border border-gray-200 bg-white p-12 text-center">
    <p className="text-gray-500">Nothing yet.</p>
  </div>
)}
```

**Fix:** A shared component `frontend/src/components/ui/PageState.tsx`:

```tsx
interface PageStateProps {
  isLoading: boolean;
  error: Error | null;
  isEmpty: boolean;
  errorFallback?: string;
  emptySlot?: React.ReactNode;
}

export default function PageState({ isLoading, error, isEmpty, errorFallback, emptySlot }: PageStateProps) {
  if (isLoading) return (
    <div className="flex flex-col items-center py-20">
      <Spinner size="lg" />
      <WakeupHint />
    </div>
  );
  if (error) return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      {error instanceof Error ? error.message : (errorFallback ?? "Something went wrong.")}
    </div>
  );
  if (isEmpty) return (
    <div className="rounded-xl border border-gray-200 bg-white p-12 text-center">
      {emptySlot ?? <p className="text-gray-500">No items yet.</p>}
    </div>
  );
  return null;
}
```

Each page replaces the three blocks with:
```tsx
<PageState
  isLoading={isLoading}
  error={error as Error | null}
  isEmpty={data?.items.length === 0}
  errorFallback="Failed to load history."
  emptySlot={<p className="text-gray-500">No verifications yet.</p>}
/>
```

---

## F-11 — STRUCTURAL — `ProtectedRoute` / `AdminRoute` spinner JSX

**Importance: 3/10**

**Files:**
- `frontend/src/components/ProtectedRoute.tsx` lines 8–13
- `frontend/src/components/AdminRoute.tsx` lines 8–13

**Duplication: 100% (5 lines)**

```tsx
if (isLoading) {
  return (
    <div className="flex h-screen items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}
```

**Fix:** Extract a `FullScreenSpinner` component or reuse from an existing shared component:

```tsx
// ui/FullScreenSpinner.tsx
export default function FullScreenSpinner() {
  return (
    <div className="flex h-screen items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}
```

Both route guards replace the `if (isLoading)` block with `if (isLoading) return <FullScreenSpinner />;`.

---

## F-12 — STRUCTURAL — Paginated GET service methods

**Importance: 3/10**

**Files:**
- `frontend/src/services/verificationService.ts` — `listVerifications(page, pageSize = 20)`
- `frontend/src/services/batchService.ts` — `listJobs(page, pageSize = 20)`
- `frontend/src/services/adminService.ts` — `listAllVerifications(page, pageSize = 20)`

**Duplication: ~70% (same signature, same `{ params: { page, page_size: pageSize } }` call shape)**

```typescript
// Identical in all three services (only the URL differs)
const res = await api.get<PaginatedResponse<T>>(
  "/v1/.../",
  { params: { page, page_size: pageSize } },
);
return res.data;
```

**Fix:** A shared utility in `services/api.ts`:

```typescript
export async function getPaginated<T>(
  path: string,
  page: number,
  pageSize = 20,
): Promise<PaginatedResponse<T>> {
  const res = await api.get<PaginatedResponse<T>>(path, {
    params: { page, page_size: pageSize },
  });
  return res.data;
}
```

Each service method becomes a one-liner:
```typescript
listVerifications: (page = 1, pageSize = 20) =>
  getPaginated<VerificationSummary>("/v1/verifications", page, pageSize),
```

---

## F-13 — DATA — Mock LLM client in two integration test files

**Importance: 4/10**

**Files:**
- `backend/tests/integration/test_verification_pipeline.py`
- `backend/tests/integration/test_batch_pipeline.py`

Both files define a helper that builds a mock Anthropic client returning a canned `LLMAnalysisResult` JSON response. If the `LLMAnalysisResult` schema changes (e.g. a new field is added), both mock factories must be updated.

**Fix:** Move the mock factory to `backend/tests/conftest.py` or a new `backend/tests/fixtures/llm.py` and import it in both test files:

```python
# tests/fixtures/llm.py
from unittest.mock import AsyncMock, MagicMock

def make_mock_llm_client(response_json: str | None = None) -> MagicMock:
    default = '{"person_found": "yes", ...}'
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=MagicMock(
        content=[MagicMock(text=response_json or default)]
    ))
    return client
```

---

## Remediation Priority

| Priority | Finding | Why |
|----------|---------|-----|
| 1st | F-01 | Service constructor changes touch both task files — already a pain point |
| 2nd | F-02 | New `VerificationResult` fields need 2 edits — happened before (confidence_level was added) |
| 3rd | F-04 | Normalisation logic is subtle; one file could drift |
| 4th | F-07 | `PaginatedResponse.build()` is pure addition, zero risk |
| 5th | F-03 | One-liner utility, no risk |
| Later | F-05, F-06, F-08, F-09 | Sound improvements, slightly more involved |
| Optional | F-10, F-11, F-12, F-13 | Frontend/test quality improvements; no correctness risk |
