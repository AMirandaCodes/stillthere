# Complexity Audit

**Date:** 2026-07-06  
**Scope:** Full codebase — `backend/app/` and `frontend/src/`  
**Method:** Manual static analysis across cyclomatic complexity, cognitive complexity, lines-of-code, coupling, and cohesion dimensions.

---

## Summary table

| ID   | Location | Category | Importance |
|------|----------|----------|-----------|
| C-01 | `batch_tasks.py:126` `_process_batch_row_async` | Cyclomatic + Cognitive | 8/10 |
| C-02 | `batch_service.py:118` `BatchService.upload` | LOC + Cohesion | 7/10 |
| C-03 | `verification_tasks.py:208` `_run_verification_async` | LOC + Cognitive | 6/10 |
| C-04 | `llm_service.py:147` `LLMService.build_prompt` | LOC + Cognitive | 5/10 |
| C-05 | `batch_service.py:299` `export_csv_stream` | LOC + Cognitive | 5/10 |
| C-06 | `api/v1/routes/admin.py:29` | Coupling + Cohesion | 5/10 |
| C-07 | `VerificationResultPage.tsx:30` | LOC + Cognitive | 5/10 |
| C-08 | `api/deps.py` (whole file) | Cohesion | 4/10 |
| C-09 | `context/AuthContext.tsx:43` `logout` | Coupling | 3/10 |
| C-10 | `routes/verifications.py:1`, `routes/batch.py:5` | Unused imports | 2/10 |

---

## C-01 — `_process_batch_row_async` has cyclomatic complexity ≈ 11

**File:** `backend/app/tasks/batch_tasks.py:126–238`  
**Importance:** 8/10

**Problem:**  
113 lines, three nested session blocks, and 9 distinct conditional branches. Cyclomatic complexity ≈ 11. The crash-recovery block for `VerificationStatus.RUNNING` (lines 163–177) is buried deep inside Session 1 alongside idempotency checks, making the idempotency logic hard to scan.

Branching inventory in Session 1 alone (lines 131–177):
- `jr is None` → error return
- `jr.status != PENDING` → skip return
- `jr.verification_result_id is None` → error return
- `ver is None` → return
- `ver.status == COMPLETE` → reconcile + return
- `ver.status == RUNNING` → crash-recovery delete

**Remediation — extract crash-recovery and reconcile blocks into helpers:**

```python
# NEW helper (add above _process_batch_row_async)
async def _reconcile_already_complete(
    session: AsyncSession,
    jr: JobResult,
    ver: VerificationResult,
    job_uuid: UUID,
) -> None:
    """JobResult update was lost after a successful pipeline run — reconcile now."""
    jr.status = JobResultStatus.SUCCESS
    await session.commit()
    await _increment_counters(job_uuid, failed=False, unclear=(ver.confidence_score == 0))


async def _clear_partial_evidence(session: AsyncSession, ver: VerificationResult) -> None:
    """Crash recovery: delete any evidence written before the worker died."""
    await session.execute(
        delete(EvidenceSource).where(EvidenceSource.verification_result_id == ver.id)
    )
    logger.info("Crash recovery: cleared partial evidence", verification_result_id=str(ver.id))
```

Then the Session 1 block collapses from 47 lines to ≈ 25 lines, with each branch being a single delegating call.

---

## C-02 — `BatchService.upload` is 106 lines with two distinct responsibilities

**File:** `backend/app/services/batch_service.py:118–223`  
**Importance:** 7/10

**Problem:**  
The method does two independent things:

1. **CSV → DB records** (lines 118–192): decode, validate, create `BatchJob`, loop through rows, create `JobResult` per row, commit.
2. **Task dispatch** (lines 195–221): call `process_batch_job.delay()`, store `celery_task_id`, handle dispatch failure.

The dispatch block is 27 lines with a nested try/except and two separate `commit()` calls. If dispatch fails, it updates `status=FAILED` — a state transition that belongs in a named method.

**Remediation — extract `_dispatch_job`:**

```python
async def _dispatch_job(self, batch_job: BatchJob) -> None:
    """Dispatch the Celery task and store task_id; mark FAILED if dispatch fails."""
    from app.tasks.batch_tasks import process_batch_job
    try:
        task = process_batch_job.delay(str(batch_job.id))
        await self._session.execute(
            update(BatchJob)
            .where(BatchJob.id == batch_job.id)
            .values(celery_task_id=str(task.id))
        )
        await self._session.commit()
        logger.info("Batch job dispatched", batch_job_id=str(batch_job.id), task_id=str(task.id))
    except Exception as exc:
        logger.error("Failed to dispatch batch job", batch_job_id=str(batch_job.id), error=str(exc))
        await self._session.execute(
            update(BatchJob).where(BatchJob.id == batch_job.id).values(status=BatchJobStatus.FAILED)
        )
        await self._session.commit()

# upload() then ends with:
await self._session.commit()        # existing records commit
await self._dispatch_job(batch_job) # replaces lines 194-221
await self._session.refresh(batch_job)
return BatchJobResponse.model_validate(batch_job)
```

This brings `upload` down to ≈ 75 lines and makes dispatch failure handling independently testable.

---

## C-03 — `_run_verification_async` is 95 lines of inter-session orchestration

**File:** `backend/app/tasks/verification_tasks.py:208–303`  
**Importance:** 6/10

**Problem:**  
95 lines, 3 separate `AsyncSessionLocal()` contexts, and the idempotency/crash-recovery block in Phase 1 has 4 branches (COMPLETE, FAILED, RUNNING, PENDING). The phase structure is clearly commented, which lowers cognitive load, but the function still exceeds the 50-line guideline significantly. A reader must hold all 4 phase contracts in mind simultaneously.

Phase 1 alone is 26 lines (224–248) and handles both "should we run at all?" and "clean up previous run".

**Remediation — extract Phase 1 into `_check_and_set_running`:**

```python
async def _check_and_set_running(result_uuid: UUID, result_id: str) -> bool:
    """
    Idempotency guard + RUNNING transition.
    Returns False if the task should be skipped (already terminal or not found).
    """
    async with AsyncSessionLocal() as session:
        result = await session.get(VerificationResult, result_uuid)
        if result is None:
            logger.error("VerificationResult not found — aborting", result_id=result_id)
            return False
        if result.status in (VerificationStatus.COMPLETE, VerificationStatus.FAILED):
            logger.info("Already terminal — skipping", result_id=result_id, status=result.status)
            return False
        if result.status == VerificationStatus.RUNNING:
            await session.execute(
                delete(EvidenceSource).where(EvidenceSource.verification_result_id == result_uuid)
            )
            logger.info("Crash recovery: cleared partial evidence", result_id=result_id)
        result.status = VerificationStatus.RUNNING
        await session.commit()
    return True
```

`_run_verification_async` then starts with:
```python
if not await _check_and_set_running(result_uuid, result_id):
    return
```
Reducing the function to ≈ 60 lines and making the guard independently testable.

---

## C-04 — `LLMService.build_prompt` is an 84-line string builder with nested loops

**File:** `backend/app/services/llm_service.py:147–230`  
**Importance:** 5/10

**Problem:**  
Two nested for-loops (search hits, then pages) and an inline `schema_example` dict spanning 17 lines (188–207) are embedded in a single method. Cognitive complexity is moderate but the method is hard to unit-test for just one section of the prompt.

**Remediation — extract the schema constant and two evidence formatters:**

```python
_PROMPT_SCHEMA = {
    "person_found": "yes | no | unclear",
    "appears_associated": "yes | no | unclear",
    # ... (move the dict out of the method entirely as a module-level constant)
}

@staticmethod
def _format_search_evidence(hits: list) -> list[str]:
    lines = []
    for hit in hits[:15]:
        lines += [f"[Search result — {hit.query_type}]", f"Title: {hit.title}",
                  f"URL: {hit.url}", f"Snippet: {hit.snippet}", ""]
    return lines

@staticmethod
def _format_page_evidence(pages: list) -> list[str]:
    lines = []
    for page in pages:
        if page.fetch_ok and page.text:
            lines += [f"[Page content from {page.url}]", f"Title: {page.title}",
                      page.text[:3_000], ""]
    return lines
```

`build_prompt` then delegates to these, dropping to ≈ 30 lines.

---

## C-05 — `export_csv_stream` has 12 conditional `vr.X if vr else ""` expressions in one row

**File:** `backend/app/services/batch_service.py:299–371`  
**Importance:** 5/10

**Problem:**  
The `writer.writerow([...])` call on lines 349–363 has 12 elements, 7 of which are `vr.X.value if vr else ""` ternary expressions. Reading this list requires mentally tracking whether `vr` is None across all 7 fields simultaneously. The overall generator is 72 lines with a nested while-loop + for-loop.

**Remediation — extract `_csv_row`:**

```python
def _csv_row(jr: JobResult) -> list:
    raw = jr.raw_csv_row or {}
    vr = jr.verification_result
    return [
        jr.row_number,
        raw.get("name", ""),
        raw.get("company", ""),
        raw.get("email", ""),
        jr.status.value,
        vr.person_found.value       if vr else "",
        vr.appears_associated.value if vr else "",
        vr.found_on_website.value   if vr else "",
        vr.company_active.value     if vr else "",
        vr.email_match.value        if vr else "",
        vr.confidence_score         if vr else "",
        vr.confidence_level.value   if vr else "",
        jr.error_message or (vr.error_message if vr else "") or "",
    ]
```

The inner loop then becomes `writer.writerow(_csv_row(jr))`. This also makes per-row formatting independently testable.

---

## C-06 — `admin.py` route bypasses the service layer and duplicates `PaginatedResponse` construction

**File:** `backend/app/api/v1/routes/admin.py:29–52`  
**Importance:** 5/10

**Problem:**  
Three issues in one route:

1. **Bypasses service layer.** The route instantiates `VerificationRepository` directly and builds `AdminVerificationSummary` items inline. Every other route delegates all business logic to a service class. The admin route is the only exception.

2. **Does not use `PaginatedResponse.build()`.** The response is constructed manually:
   ```python
   return PaginatedResponse(
       items=items,
       total=total,
       page=pagination.page,
       page_size=pagination.page_size,
       total_pages=math.ceil(total / pagination.page_size) if total and pagination.page_size else 0,
   )
   ```
   `PaginatedResponse.build()` was added specifically to eliminate this pattern.

3. **Unused `import math`.** The `math` import is only needed by the manual `ceil` call above; using `.build()` removes the need for it.

**Remediation — add `AdminService` or extend `VerificationService`:**

Option A (minimal): move mapping into `VerificationService.list_all_results()` and call `PaginatedResponse.build()`.

```python
# In VerificationService:
async def list_all_results(
    self, offset: int, limit: int
) -> PaginatedResponse[AdminVerificationSummary]:
    results, total = await self._verifications.list_all_with_user(offset, limit)
    items = [
        AdminVerificationSummary(
            id=r.id, search_id=r.search_id, status=r.status,
            full_name=r.search.contact.full_name,
            company_name=r.search.company.name,
            work_email=r.search.submitted_email,
            user_email=r.search.user.email if r.search.user else None,
            confidence_score=r.confidence_score,
            confidence_level=r.confidence_level,
            created_at=r.created_at,
        )
        for r in results
    ]
    return PaginatedResponse.build(items=items, total=total, offset=offset, limit=limit)
```

The route then becomes:
```python
# admin.py — no math import, no repository import, no inline mapping
async def list_all_verifications(pagination, db, _) -> PaginatedResponse[AdminVerificationSummary]:
    return await VerificationService(db).list_all_results(pagination.offset, pagination.page_size)
```

---

## C-07 — `VerificationResultPage.tsx` is 200 lines with an IIFE for useful links

**File:** `frontend/src/pages/VerificationResultPage.tsx:30–200`  
**Importance:** 5/10

**Problem:**  
200 lines in a single component, rendering 4 mutually exclusive states (loading, error, pending, complete) plus 4 complete-state sub-sections. The useful links section (lines 171–195) uses an immediately-invoked function expression (`{(() => { ... })()`), which is an unusual pattern that breaks JSX readability and cannot be tested in isolation.

The inline `triStateRows` type annotation on lines 14–20 is also unnecessarily verbose — the key type can be simplified.

**Remediation — two changes:**

1. Replace the IIFE with a named component or `useMemo`:

```tsx
// Replace lines 171-195 with:
const validLinks = Object.entries(data.useful_links).filter(
  ([, url]) => typeof url === "string" && /^https?:\/\//i.test(url)
);

{validLinks.length > 0 && (
  <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
    <h2 className="mb-4 font-semibold text-gray-800">Useful Links</h2>
    <ul className="space-y-2">
      {validLinks.map(([label, url]) => (
        <li key={label}>
          <a href={url} target="_blank" rel="noopener noreferrer"
             className="inline-flex items-center gap-1.5 text-sm text-brand-600 hover:underline">
            <ExternalLink className="h-3.5 w-3.5" />
            {label}
          </a>
        </li>
      ))}
    </ul>
  </div>
)}
```

2. Simplify the `triStateRows` type to avoid the long inline mapped type:

```tsx
// Replace lines 14-20 with:
type TriStateKey = "person_found" | "appears_associated" | "found_on_website" | "company_active" | "email_match";
const triStateRows: { key: TriStateKey; label: string }[] = [ ... ];
```

For future work (not urgent): extract `<EvidenceSourcesList>` and `<TriStateResults>` as separate components to bring the page under 100 lines.

---

## C-08 — `deps.py` has low cohesion: 5 unrelated concerns in one file

**File:** `backend/app/api/deps.py` (whole file)  
**Importance:** 4/10

**Problem:**  
`deps.py` is the FastAPI "grab-bag" — a common pattern, but this file contains:

1. Pagination (`PaginationParams`)
2. Required auth (`get_current_user`, `CurrentUser`)
3. Admin auth (`get_current_admin`, `CurrentAdmin`)
4. Optional auth (`get_optional_user`, `OptionalUser`)
5. Cache (`get_cache`, `CacheDep`)
6. Verification rate limiting (`_check_verification_limit`, `VerificationRateLimit`)
7. Batch rate limiting (`_check_batch_limit`, `BatchRateLimit`)

This is not a correctness problem — FastAPI projects commonly accumulate here — but the file is a navigation bottleneck: every route imports from it, and finding a specific dependency requires scanning all 7 concerns.

**Remediation (low priority):** Split by concern:

```
api/
  deps/
    __init__.py       # re-exports everything (backward compat)
    auth.py           # get_current_user, get_optional_user, get_current_admin
    pagination.py     # PaginationParams
    cache.py          # get_cache
    rate_limit.py     # _check_verification_limit, _check_batch_limit
```

The `__init__.py` re-exports keep all existing `from app.api.deps import X` imports unchanged — no other files need editing.

---

## C-09 — `AuthContext.logout` reads `localStorage` directly instead of via `authService`

**File:** `frontend/src/context/AuthContext.tsx:43–47`  
**Importance:** 3/10

**Problem:**  
`logout` reaches into `localStorage` directly to get the refresh token:
```ts
const refreshToken = localStorage.getItem("stillthere_refresh_token") ?? "";
await authService.logout(refreshToken);
```

The localStorage key `"stillthere_refresh_token"` is a magic string duplicated between `AuthContext` and `authService`. If the key ever changes in `authService`, `AuthContext` becomes silently broken.

**Remediation — expose `getRefreshToken()` in `authService` alongside `getToken()`:**

```ts
// In authService.ts:
getRefreshToken: () => localStorage.getItem("stillthere_refresh_token"),

// In AuthContext.tsx:
async function logout() {
  const refreshToken = authService.getRefreshToken() ?? "";
  await authService.logout(refreshToken);
  setUser(null);
}
```

---

## C-10 — Unused `import math` in two route files

**Files:** `backend/app/api/v1/routes/verifications.py:1`, `backend/app/api/v1/routes/batch.py:5`  
**Importance:** 2/10

**Problem:**  
Both route files imported `math` for manual `math.ceil(total / page_size)` pagination calculations. After the `PaginatedResponse.build()` refactor (duplication audit), those calculations moved into the service layer. The `math` import is now dead in both files.

**Remediation:**

```python
# verifications.py — remove line 1:
import math  # DELETE

# batch.py — remove line 5:
import math  # DELETE
```

Running `npm run lint` (frontend) and `pytest` will not catch this — use `ruff check` or `flake8` in the backend container:
```bash
docker compose exec backend ruff check app/api/v1/routes/
```

---

## Coupling summary

| Module | Afferent (Ca) | Efferent (Ce) | Instability (I) | Assessment |
|--------|--------------|--------------|-----------------|------------|
| `models/enums.py` | ~15 | 0 | 0.00 | Stable — correct |
| `core/config.py` | ~10 | 1 | 0.09 | Stable — correct |
| `core/utils.py` | 4 | 1 | 0.20 | Stable |
| `repositories/base.py` | 4 | 3 | 0.43 | Balanced |
| `services/verification_service.py` | 3 | 6 | 0.67 | Slightly instable but expected for a service |
| `services/batch_service.py` | 2 | 9 | 0.82 | High efferent; tolerable for orchestration |
| `tasks/verification_tasks.py` | 2 | 8 | 0.80 | High efferent; tolerable for tasks |
| `tasks/batch_tasks.py` | 1 | 8 | 0.89 | Maximally instable; normal for a leaf task |
| `api/deps.py` | ~8 | 7 | 0.47 | Central hub; splitting (C-08) would lower Ca |

No circular dependencies were found. The one cross-service import (`batch_service` → `verification_service._build_summary`) is safe: `verification_service` does not import from `batch_service`.

---

## Files exceeding size guidelines

| File | Lines | Guideline exceeded |
|------|-------|--------------------|
| `backend/app/services/batch_service.py` | 371 | >300 lines |
| `backend/app/tasks/verification_tasks.py` | 322 | >300 lines |
| `backend/app/tasks/batch_tasks.py` | 298 | approaching 300 |
| `backend/app/services/llm_service.py` | 270 | — |
| `frontend/src/pages/VerificationResultPage.tsx` | 200 | — |

The 371-line `batch_service.py` is the primary candidate for splitting. The natural cut is: move `parse_csv`, `validate_columns`, and `clean` into `backend/app/core/csv_utils.py` (pure functions, independently testable), and move `export_csv_stream` into a dedicated `BatchExportService` or standalone module. This would bring `batch_service.py` to approximately 200 lines.
