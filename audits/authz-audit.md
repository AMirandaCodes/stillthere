# Authorization Implementation Audit

**Date:** 2026-07-07  
**Scope:** All FastAPI routes, service layer, and repository layer across the full backend  
**Auditor:** Claude Code (claude-sonnet-4-6)  
**Prior audit:** `auth-security-audit.md` (AuthN focus)

---

## Route Map — Authorization Surface

| Route | Auth required | Scope / ownership check |
|---|---|---|
| `GET /api/v1/health` | None | — |
| `GET /api/v1/health/db` | None | — |
| `POST /api/v1/auth/register` | None | — |
| `POST /api/v1/auth/login` | None | — |
| `POST /api/v1/auth/refresh` | None (RT credential) | — |
| `POST /api/v1/auth/logout` | None (RT credential) | — |
| `GET /api/v1/auth/me` | `CurrentUser` | own record only |
| `POST /api/v1/verifications` | `OptionalUser` | guest-allowed by design |
| `GET /api/v1/verifications/{id}` | **None** | no ownership check (by design, UUID entropy) |
| `GET /api/v1/verifications` | `CurrentUser` | scoped to `user_id` ✓ |
| `GET /api/v1/contacts` | `CurrentUser` | **no user_id scope** |
| `GET /api/v1/contacts/{id}` | `CurrentUser` | **no user_id scope; returns other users' results** |
| `GET /api/v1/companies` | `CurrentUser` | **no user_id scope** |
| `GET /api/v1/companies/{id}` | `CurrentUser` | **no user_id scope** |
| `POST /api/v1/batch/upload` | `CurrentUser` | user_id stored on job ✓ |
| `GET /api/v1/batch` | `CurrentUser` | scoped to `user_id` ✓ |
| `GET /api/v1/batch/{id}` | `CurrentUser` | ownership check ✓ |
| `GET /api/v1/batch/{id}/results` | `CurrentUser` | ownership pre-checked via `get_job` ✓ |
| `GET /api/v1/batch/{id}/export` | `CurrentUser` | ownership pre-checked via `get_job` ✓ |
| `GET /api/v1/admin/verifications` | `CurrentAdmin` | all tenants (intentional) ✓ |

---

## Findings

### AZ-01 — Cross-User Verification Results via Contact Detail Endpoint

**Severity:** High  
**CWE:** CWE-639 Authorization Bypass Through User-Controlled Key

**Evidence:**

| Location | What |
|---|---|
| `backend/app/api/v1/routes/contacts.py:38-49` | Route passes no `user_id` to service |
| `backend/app/services/contact_service.py:62-78` | Iterates `contact.searches` unfiltered |
| `backend/app/repositories/contact_repository.py:104-120` | `selectinload(Contact.searches)` has no `WHERE user_id=` clause |

**Why it matters:**

Contacts are shared objects (deduped by email across all users). The `Search` model links contacts to users via `Search.user_id`. `ContactService.get()` loads **all** searches for a contact, then builds `recent_verifications` from those searches — regardless of which user submitted them.

Any authenticated user can retrieve another user's verification results by iterating contact UUIDs via `GET /api/v1/contacts` (paginated list) and then calling `GET /api/v1/contacts/{id}`.

**Concrete data exposed in `VerificationSummary`:**
- `full_name`, `company_name`, `work_email`
- `status`, `confidence_score`, `confidence_level`
- `created_at`

**Reproduction steps:**

```bash
# User A submits a verification
curl -X POST /api/v1/verifications \
  -H "Authorization: Bearer <user_a_token>" \
  -d '{"full_name":"Jane Doe","company_name":"Acme","work_email":"jane@acme.com"}'
# → creates Contact(email=jane@acme.com, id=UUID-X)

# User B lists contacts and sees all contacts including User A's
curl /api/v1/contacts -H "Authorization: Bearer <user_b_token>"
# → lists UUID-X in results

# User B retrieves User A's verification result
curl /api/v1/contacts/UUID-X -H "Authorization: Bearer <user_b_token>"
# → recent_verifications[] contains User A's verification with confidence score
```

**Remediation:**

Option A (minimal, recommended): filter searches by `user_id` in the service layer.

```python
# backend/app/services/contact_service.py — ContactService.get()
async def get(self, contact_id: UUID, user_id: UUID | None = None) -> ContactResponse | None:
    contact = await self._repo.get_with_recent_searches(contact_id)
    if contact is None:
        return None
    user_searches = [
        s for s in contact.searches
        if user_id is None or s.user_id == user_id
    ]
    for search in sorted(user_searches, key=lambda s: s.created_at, reverse=True)[:10]:
        ...
```

Then pass `current_user.id` from the route:

```python
# backend/app/api/v1/routes/contacts.py
async def get_contact(contact_id: UUID, db: DbSession, current_user: CurrentUser):
    result = await ContactService(db).get(contact_id, user_id=current_user.id)
```

Option B (deeper defence): push the filter into `get_with_recent_searches` via a joined WHERE clause. This avoids loading the extra rows from the DB at all.

**Defence in depth:** The `GET /api/v1/contacts` list also exposes platform-wide contact metadata (see AZ-03). Address AZ-01 first — it leaks verification results; AZ-03 leaks only names/emails.

---

### AZ-02 — Unauthenticated Verification Detail Exposes PII (Medium)

**Severity:** Medium  
**CWE:** CWE-284 Improper Access Control

**Evidence:**

| Location | What |
|---|---|
| `backend/app/api/v1/routes/verifications.py:54-80` | `get_verification` has no auth dependency |
| `backend/app/schemas/verification.py` | `VerificationResultResponse` includes full_name, work_email, evidence |

**Why it matters:**

`GET /api/v1/verifications/{verification_id}` is intentionally unauthenticated (comment: SEC-05 Option B — UUID v4 entropy as capability URL). The response includes:

- `full_name` and `work_email` of the person being verified (PII)
- Full evidence sources: scraped URLs, page titles, analysis text
- Confidence score and reasoning

The UUID-as-secret model is reasonable for short-lived polling but has persistent exposure risk: the UUID is stored in browser history, proxy logs, and any URL-forwarding service. There is no expiry on verification results. There is also no rate limit on this endpoint — a leaked UUID can be retrieved indefinitely by anyone.

**Reproduction steps:**

1. User A submits `POST /api/v1/verifications` → receives `verification_id=UUID-X`
2. UUID-X is visible in browser history, proxy logs, or shared in a message
3. Any unauthenticated party calls `GET /api/v1/verifications/UUID-X` → full PII and evidence

**Remediation:**

Minimal (current design preserved): add rate limiting to prevent bulk scraping even without enumeration.

```python
# backend/app/api/v1/routes/verifications.py
from app.core.rate_limiting import limiter

@router.get("/{verification_id}", ...)
@limiter.limit("30/minute")
async def get_verification(request: Request, verification_id: UUID, ...) -> VerificationResultResponse:
    ...
```

Stronger (if guest mode can be rearchitected): require auth, or scope the endpoint to the submitting user's session. Since `POST /verifications` stores `user_id` on the Search record, an authenticated user can only retrieve their own verifications.

Note: AZ-02 is lower priority than AZ-01 because UUID entropy does make passive discovery infeasible. The risk is active disclosure of a known UUID.

---

### AZ-03 — Contacts and Companies Lists Expose Platform-Wide Directory (Low)

**Severity:** Low  
**CWE:** CWE-200 Exposure of Sensitive Information to Unauthorized Actor

**Evidence:**

| Location | What |
|---|---|
| `backend/app/api/v1/routes/contacts.py:13-30` | `list_contacts` passes no `user_id` to service |
| `backend/app/services/contact_service.py:33-59` | `ContactService.list()` calls `list_with_verification_count` unfiltered |
| `backend/app/repositories/contact_repository.py:58-75` | JOIN has no `WHERE user_id=` clause |
| `backend/app/api/v1/routes/companies.py:13-28` | `list_companies` passes no `user_id` |

**Why it matters:**

Any authenticated user can enumerate ALL contacts and companies that have ever been verified by any user on the platform:
- `GET /api/v1/contacts` returns every contact's name, email, and total verification count
- `GET /api/v1/companies` returns every company's name, domain, website, and total verification count

Email addresses of the subjects being verified (not the platform's users) are exposed. In a strict multi-tenant deployment this constitutes cross-tenant data leakage. For a single-organisation deployment it may be intentional (shared team directory), but that intent is not documented.

The `total_verifications` count on contacts/companies also aggregates across all users, revealing platform-wide usage patterns.

**Reproduction steps:**

```bash
curl /api/v1/contacts?page_size=100 -H "Authorization: Bearer <any_valid_token>"
# Returns all contacts ever submitted — email addresses included
```

**Remediation:**

If this is intentional (shared directory within one org), document it explicitly in CLAUDE.md and in the OpenAPI schema description. If tenant isolation is required:

```python
# backend/app/api/v1/routes/contacts.py
async def list_contacts(pagination, db, current_user: CurrentUser, q=None):
    return await ContactService(db).list(
        ...,
        user_id=current_user.id,   # <-- add
    )
```

Add a `user_id` parameter to `ContactService.list()` and `ContactRepository.list_with_verification_count()`, filtering by `Search.user_id` via a subquery or JOIN.

---

### AZ-04 — Admin Endpoint Has No Rate Limit (Low)

**Severity:** Low  
**CWE:** N/A

**Evidence:**

| Location | What |
|---|---|
| `backend/app/api/v1/routes/admin.py:14-30` | No `@limiter.limit()` decorator |
| `backend/app/main.py:164-170` | Admin router has no SlowAPI config |

**Why it matters:**

`GET /api/v1/admin/verifications` is protected by `CurrentAdmin` (requires `is_admin=True`). A compromised admin account can execute unlimited paginated queries, causing sustained high-load DB reads. All other sensitive endpoints have explicit rate limits.

**Remediation:**

```python
# backend/app/api/v1/routes/admin.py
from fastapi import Request
from app.core.rate_limiting import limiter

@router.get("/verifications", ...)
@limiter.limit("60/minute")
async def list_all_verifications(
    request: Request,
    pagination: PaginationDep,
    db: DbSession,
    _: CurrentAdmin,
) -> PaginatedResponse[AdminVerificationSummary]:
```

---

### AZ-05 — `get_job_results` Lacks Service-Level Ownership Guard (Informational)

**Severity:** Informational  
**CWE:** CWE-639

**Evidence:**

| Location | What |
|---|---|
| `backend/app/services/batch_service.py:280-307` | `get_job_results` takes only `job_id`, no `user_id` |
| `backend/app/api/v1/routes/batch.py:97-113` | Ownership pre-checked via `get_job` before calling `get_job_results` |

**Why it matters:**

The route correctly pre-checks ownership (`get_job(job_id, user_id=current_user.id)`). But `get_job_results` at the service layer accepts any `job_id` with no ownership parameter. If a future route or background task calls `get_job_results` directly without the pre-check, it will return rows for any job without verifying ownership.

Currently safe. The risk is future regression.

**Remediation:**

Add a defensive `user_id` parameter:

```python
# backend/app/services/batch_service.py
async def get_job_results(
    self,
    job_id: UUID,
    offset: int,
    limit: int,
    user_id: UUID | None = None,  # optional but validates job ownership
) -> PaginatedResponse[JobResultResponse]:
    if user_id is not None:
        job = await self._session.get(BatchJob, job_id)
        if job is None or job.user_id != user_id:
            return PaginatedResponse.build(items=[], total=0, offset=offset, limit=limit)
    ...
```

---

### AZ-06 — DB Health Endpoint Publicly Accessible (Informational)

**Severity:** Informational  
**CWE:** CWE-200

**Evidence:**

| Location | What |
|---|---|
| `backend/app/api/v1/routes/health.py:12-18` | `db_health_check` has no auth dependency |

**Why it matters:**

`GET /api/v1/health/db` executes a live `SELECT 1` against PostgreSQL and returns `{"status": "ok", "database": "connected"}`. An unauthenticated caller can confirm the database is live. On failure, SQLAlchemy or PostgreSQL error text may surface in unhandled exceptions (mitigated by the global exception handler returning only a `request_id`). Not exploitable in isolation but constitutes unnecessary information disclosure to anonymous scanners.

**Remediation:**

For production, either remove the `/health/db` endpoint or require internal-network access only (e.g., via a load-balancer path that is not publicly routed). If it must remain public, keep it — the global exception handler already suppresses DB error detail.

---

## Summary Risk Score

**5.0 / 10**

The platform has solid batch-job IDOR protection and correct JWT validation throughout. The primary risk is AZ-01: cross-user verification data leaks through the contact detail endpoint, which is a one-function fix. AZ-02 is a design-level tradeoff that has been documented but has no mitigating rate limit. AZ-03 affects metadata only and may be intentional.

| Finding | Severity | Fix size |
|---|---|---|
| AZ-01 Cross-user results via contacts | **High** | ~10 lines |
| AZ-02 Unauthenticated PII via UUID | Medium | ~5 lines (rate limit) |
| AZ-03 Global contact/company directory | Low | ~20 lines each |
| AZ-04 No rate limit on admin endpoint | Low | 2 lines |
| AZ-05 Service-level IDOR guard missing | Info | ~10 lines |
| AZ-06 Public DB health endpoint | Info | No change required |

---

## Top 5 Prioritised Fixes

1. **AZ-01** — Filter `contact.searches` by `user_id` in `ContactService.get()` and pass `current_user.id` from the route. Single highest-impact change; fixes cross-user result leakage with ~10 lines.

2. **AZ-02** — Add `@limiter.limit("30/minute")` to `GET /verifications/{id}`. No architectural change; mitigates bulk retrieval of leaked UUIDs immediately.

3. **AZ-04** — Add `@limiter.limit("60/minute")` to `GET /admin/verifications`. Two-line change; closes the rate-unlimited admin query path.

4. **AZ-03** — Decide explicitly: shared directory (document it, mark contacts/companies as tenant-shared in OpenAPI) or scoped (add `user_id` filter to list endpoints). Either is defensible; the current state is just undocumented.

5. **AZ-05** — Add `user_id: UUID | None` to `get_job_results` with an optional ownership guard. Prevents future regressions if the method is reused.

---

## Checklist Diff

| # | Item | Result | Notes |
|---|---|---|---|
| 1 | BOLA / IDOR — ownership checks on all object-level routes | **Partial** | Batch: ✓. Contacts/companies: ✗ (AZ-01, AZ-03) |
| 2 | Broken function level auth — privileged routes have role checks | **Pass** | Admin endpoint requires `is_admin=True` from DB |
| 3 | Missing auth on sensitive endpoints | **Partial** | `GET /verifications/{id}` unauthenticated by design (AZ-02) |
| 4 | RBAC — roles enforced server-side, deny-by-default | **Pass** | `get_current_admin` checks DB; no client-set role field |
| 5 | Privilege escalation via update endpoints | **Pass** | No `PUT /users/{id}` exists; `UserCreate` has no `is_admin` |
| 6 | JWT validation on every protected route | **Pass** | `decode_access_token` validates `alg`, `iss`, `aud`, `exp`, `type`; `is_admin` from DB |
| 7 | Scope checking for API tokens | **N/A** | No API key / scope system; only user JWTs |
| 8 | Multi-tenant isolation | **Partial** | Batch: ✓. Contacts/companies: ✗ (AZ-01, AZ-03) |
| 9 | Bulk endpoint ownership per item | **Pass** | `get_job(job_id, user_id=...)` checks owner before results/export |
| 10 | Field-level authorization | **Pass** | `UserCreate` excludes `is_admin`/`is_active`; `UserResponse` exposes only own record |
| 11 | Error handling / resource enumeration | **Pass** | `get_job` returns `None` (→ 404) for other users' jobs; no 403 leak |
| 12 | Middleware ordering | **Pass** | CORS → SlowAPI → RequestID → ContentSize → SecurityHeaders → handlers |
| 13 | CORS / CSRF | **Pass** | Specific origins list; `allow_credentials=True` with no wildcard; no cookie auth |
| 14 | Open redirect protections | **N/A** | No `redirect`/`next` parameters anywhere in the API |
| 15 | Fallback / debug routes | **Pass** | No `/seed`, `/reset`, `/debug` endpoints; docs disabled in production |
