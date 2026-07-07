# Session and Cookie Security Audit

**Date:** 2026-07-07  
**Scope:** Session management, token storage, CSRF posture, cookie security  
**Architecture note:** This application uses a **stateless JWT + opaque refresh token** design. There are no traditional server-side sessions and no browser cookies whatsoever. Tokens are returned in JSON response bodies and persisted in `localStorage` by the frontend. All cookie-specific checklist items (Secure flag, HttpOnly, SameSite, domain scoping) are therefore evaluated against the token storage mechanism actually in use.

**Files examined:** `backend/app/core/auth.py`, `backend/app/services/auth_service.py`, `backend/app/api/v1/routes/auth.py`, `backend/app/api/deps/auth.py`, `backend/app/models/{user,refresh_token}.py`, `backend/app/repositories/refresh_token_repository.py`, `backend/app/main.py`, `backend/app/core/config.py`, `frontend/src/services/{api,authService}.ts`, `frontend/src/context/AuthContext.tsx`, `frontend/vite.config.ts`

---

## Summary Risk Score: 3.5 / 10

The authentication design is solid: 15-minute access tokens, 7-day database-backed refresh tokens with mandatory rotation, theft-detection triggering session-family revocation, and a `token_issued_before` column for post-password-change invalidation. No traditional session store is exposed to its default in-memory footprint. The primary risk is architectural: both tokens live in `localStorage`, which any same-origin JavaScript can read. Without a Content-Security-Policy header, a successful XSS attack exfiltrates the refresh token in addition to the short-lived access token, turning a 15-minute XSS window into a 7-day one. Adding CSP is the single highest-impact fix available.

---

## Findings

---

### SS-01 — High — CWE-922: Insecure Storage of Sensitive Information

**Title:** Both access and refresh tokens stored in `localStorage` — readable by any same-origin JavaScript

**Evidence:**

- `frontend/src/services/authService.ts:4–5`:
  ```typescript
  export const ACCESS_KEY = "stillthere_access_token";
  export const REFRESH_KEY  = "stillthere_refresh_token";
  ```
- `authService.ts:16–19` — both tokens written to `localStorage` on every login and token refresh:
  ```typescript
  setTokens(tokens: TokenResponse): void {
      localStorage.setItem(ACCESS_KEY, tokens.access_token);
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  }
  ```
- `frontend/src/services/api.ts:11–16` — access token read from `localStorage` on every request:
  ```typescript
  api.interceptors.request.use((config) => {
      const token = localStorage.getItem(ACCESS_KEY);
      if (token) config.headers.Authorization = `Bearer ${token}`;
      return config;
  });
  ```

**Why it matters:**  
`localStorage` is accessible to any JavaScript executing within the same origin. A successful XSS attack — whether via a DOM injection bug, a malicious dependency, or an unsafe `dangerouslySetInnerHTML` — can read both tokens with a single `localStorage.getItem()` call. The access token is short-lived (15 min), but the **refresh token is valid for 7 days** and rotates on each use. Exfiltrating the refresh token turns a brief XSS moment into a persistent account takeover.

The alternative, `HttpOnly` cookies, prevents JavaScript from reading the token at all — XSS can still forge requests using the cookies, but it cannot extract and exfiltrate the token value.

**Exploitability:**  
Requires an XSS vector first. The current backend has strong input sanitisation; this is a defence-in-depth gap rather than a standalone exploit. Exploitability rises significantly if the frontend ever renders user-supplied content without escaping (the `full_name` field, batch filenames, verification results from LLM output).

**PoC (no real credentials):**
```javascript
// Run from browser console or any XSS gadget on the same origin:
const tokens = {
    access:  localStorage.getItem("stillthere_access_token"),
    refresh: localStorage.getItem("stillthere_refresh_token"),
};
// tokens.refresh is now exfiltrated — valid for 7 more days from last rotation
```

**Remediation — preferred (httpOnly cookie for refresh token):**

Keep the access token in JavaScript memory only (a module-level variable, never persisted). Store only the refresh token in a cookie with `HttpOnly`, `Secure`, and `SameSite=Strict`. Add a CSRF token (double-submit cookie pattern) for the `/auth/refresh` and `/auth/logout` endpoints that the cookie reaches.

Backend: add a `Set-Cookie` response header on login/refresh, and read the refresh token from the cookie rather than the request body on `/refresh`.

```python
# In auth route — login response
from fastapi import Response

@router.post("/login")
async def login(request: Request, payload: LoginRequest, db: DbSession, response: Response) -> dict:
    tokens = await AuthService(db).login(payload.email, payload.password)
    response.set_cookie(
        "stillthere_refresh",
        tokens.refresh_token,
        httponly=True,
        secure=True,          # HTTPS only
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",  # scoped to auth routes only
    )
    return {"access_token": tokens.access_token, "token_type": "bearer", "expires_in": tokens.expires_in}
```

Frontend access token: store in a React ref or Zustand store, not `localStorage`. On page reload, call `/api/v1/auth/refresh` silently using the httpOnly cookie to get a fresh access token.

**Remediation — minimum (if cookie migration is deferred):**  
Keep current architecture but add Content-Security-Policy (see SS-02), restrict `localStorage` access surface, and ensure all user-supplied content is HTML-escaped before rendering.

---

### SS-02 — High — CWE-1021 (CSP enforcement gap): Missing Content-Security-Policy Header

**Title:** `SecurityHeadersMiddleware` does not set `Content-Security-Policy` — the primary browser XSS defence is absent

**Evidence:**

- `backend/app/main.py:115–133` — `SecurityHeadersMiddleware` sets five headers but omits CSP:
  ```python
  response.headers["X-Content-Type-Options"] = "nosniff"
  response.headers["X-Frame-Options"] = "DENY"
  response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
  response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
  if request.url.scheme == "https":
      response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
  # Content-Security-Policy is not set anywhere
  ```
- No CSP meta tag present in the frontend `index.html` (checked by inspection).

**Why it matters:**  
Without CSP, the browser allows inline scripts, `eval()`, and arbitrary external script loads. Any XSS gadget — including those introduced by a compromised npm dependency — executes freely. CSP is the primary technical control that limits XSS damage; combined with the `localStorage` token storage (SS-01), its absence means a single XSS payload can exfiltrate the refresh token with no browser-level obstacle.

**Exploitability:**  
Does not create a vulnerability on its own — it removes a mitigation layer that would otherwise contain XSS. Exploitability is SS-01's exploitability minus the protection CSP would have provided.

**Remediation:**

Add to `SecurityHeadersMiddleware`. Start with a strict policy and relax only what Vite/React requires:

```python
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    _CSP = (
        "default-src 'self'; "
        "script-src 'self'; "          # no inline scripts, no eval
        "style-src 'self' 'unsafe-inline'; "  # Tailwind injects inline styles
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "         # API calls only to same origin
        "frame-ancestors 'none'; "     # supersedes X-Frame-Options
        "base-uri 'self'; "
        "form-action 'self';"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self._CSP
        # ... existing headers
        return response
```

**Note on `'unsafe-inline'` for styles:** Tailwind v3 with the JIT compiler injects some inline styles. If `'unsafe-inline'` is unacceptable for styles, switch to Tailwind's `nonce`-based mode. For scripts, `'unsafe-inline'` must not be added — React's compiled bundles use no inline scripts.

**Verify your policy** using the browser's CSP violations console or `report-uri` before enforcing it in production.

---

### SS-03 — Medium — CWE-613: Insufficient Session Expiration

**Title:** Access tokens remain valid for up to 15 minutes after logout — no access token revocation

**Evidence:**

- `backend/app/services/auth_service.py:106–108`:
  ```python
  async def logout(self, raw_token: str) -> None:
      """Revoke the submitted refresh token. Silent if already invalid."""
      await self._tokens.revoke(hash_token(raw_token))
  ```
  Logout revokes only the refresh token. The access token is not touched.

- `backend/app/core/config.py:54`: `ACCESS_TOKEN_EXPIRE_MINUTES: int = 15` — the window an attacker retains.

- `backend/app/api/deps/auth.py:31–45`: `decode_access_token()` is purely cryptographic — no DB lookup, no revocation check.

- The `token_issued_before` mechanism (`auth.py:63–71`) covers password-change invalidation but is **not updated on logout**:
  ```python
  # auth_service.py logout() — does NOT update token_issued_before
  ```

**Why it matters:**  
If an attacker captures a valid access token (via log exposure as noted in LM-03, or XSS), the user logging out does not invalidate that token. The attacker retains API access for the remainder of the 15-minute window. For most threat models a 15-minute residual window is acceptable, but it is a gap worth documenting.

**Exploitability:**  
Low-to-moderate. Requires prior token exfiltration. Fifteen minutes is a short window but enough to enumerate verifications or submit new ones under the victim's account.

**Remediation — Option A (Redis blocklist, recommended):**

On logout, add the access token's `jti` (JWT ID) claim to a Redis key with a TTL equal to its remaining lifetime. Check the blocklist in `decode_access_token()`.

```python
# auth.py — add jti to payload
import uuid
payload["jti"] = str(uuid.uuid4())

# deps/auth.py — check blocklist after decode
jti = payload.get("jti")
if jti:
    is_revoked = await redis.exists(f"revoked_jti:{jti}")
    if is_revoked:
        raise HTTPException(401, "Token has been revoked")

# auth_service.py — logout
async def logout(self, raw_token: str, access_jti: str | None = None, access_exp: int | None = None) -> None:
    if access_jti and access_exp:
        ttl = max(0, access_exp - int(datetime.now(timezone.utc).timestamp()))
        if ttl > 0:
            await redis.setex(f"revoked_jti:{access_jti}", ttl, "1")
    await self._tokens.revoke(hash_token(raw_token))
```

**Remediation — Option B (token_issued_before on logout):**

Set `token_issued_before = now()` on the user record when they log out. This invalidates all access tokens for that user at the cost of a DB write + read on every authenticated request.

```python
# auth_service.py
async def logout(self, raw_token: str) -> None:
    revoked = await self._tokens.get_by_hash(hash_token(raw_token))
    if revoked:
        await self._users.set_token_issued_before(revoked.user_id, datetime.now(timezone.utc))
    await self._tokens.revoke(hash_token(raw_token))
```

Option A is preferred — it avoids a DB write on logout and a DB read on every API call by using Redis TTL.

---

### SS-04 — Low — CWE-613: Insufficient Session Expiration

**Title:** No absolute session lifetime — refresh tokens can be perpetually rotated indefinitely

**Evidence:**

- `backend/app/core/config.py:55`: `REFRESH_TOKEN_EXPIRE_DAYS: int = 7`
- `backend/app/services/auth_service.py:96–101`: every `/refresh` call revokes the old token and issues a new one with a fresh 7-day window.
- `backend/app/models/refresh_token.py` has `expires_at` and `revoked_at` but no "session started at" or "absolute maximum age" field.

**Why it matters:**  
An active user who calls `/refresh` daily never has to re-authenticate — their session is perpetually extended. NIST SP 800-63B (assurance level 2) and PCI-DSS 3.2.1 (requirement 8.1.8) require periodic re-authentication regardless of activity. For a contact-verification SaaS this may be acceptable commercially, but it means a compromised refresh token that is rotated daily is indefinitely valid.

**Exploitability:** Very low. Requires prior token compromise and ongoing active use to keep rotating.

**Remediation:**

Add an `issued_at` timestamp to the `RefreshToken` model (the initial issue time, not updated on rotation) and carry it forward in the rotation chain. Reject refresh attempts whose `issued_at` is older than an absolute maximum (e.g., 30 days):

```python
# models/refresh_token.py
session_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

# repositories/refresh_token_repository.py — carry forward on rotation
async def create(self, user_id, token_hash, expires_at, *, session_issued_at=None):
    token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        session_issued_at=session_issued_at or datetime.now(timezone.utc),
    )
    return await self.save(token)

# auth_service.py — check absolute age on refresh
_MAX_SESSION_DAYS = 30
if (datetime.now(timezone.utc) - stored.session_issued_at).days >= _MAX_SESSION_DAYS:
    await self._tokens.revoke(stored.token_hash)
    raise AuthError(AuthError.INVALID_TOKEN)  # client must re-login
new_token = await self._tokens.create(
    stored.user_id, new_hash, new_expiry,
    session_issued_at=stored.session_issued_at,  # carry forward
)
```

---

### SS-05 — Informational — No Concurrent Session Limit

**Title:** A single user can accumulate unlimited active refresh tokens across devices/browsers

**Evidence:**

- `backend/app/repositories/refresh_token_repository.py:14–21` — `create()` inserts a new token unconditionally; no count check or eviction of older tokens.
- `revoke_all_for_user()` exists (`refresh_token_repository.py:53–63`) and is called only on detected theft — not enforced as a policy limit.

**Why it matters:**  
Without a session cap, a single account could accumulate tokens indefinitely (an unusual user who logs in daily from different browsers without ever explicitly logging out). This has no direct security impact in most threat models but complicates account-compromise investigation (an attacker's token is invisible among legitimate ones) and adds unbounded DB growth.

**Recommendation (low priority):**

Enforce a maximum of N active refresh tokens per user on `create()`. Evict the oldest when the cap is exceeded:

```python
_MAX_SESSIONS = 5  # one per typical device

async def create(self, user_id, token_hash, expires_at, ...) -> RefreshToken:
    active = await self.session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .order_by(RefreshToken.created_at.asc())
    )
    tokens = active.scalars().all()
    if len(tokens) >= _MAX_SESSIONS:
        # Evict the oldest
        oldest = tokens[0]
        oldest.revoked_at = datetime.now(timezone.utc)
    ...
```

---

## Top 4 Prioritised Fixes

| Priority | Finding | File | Change | Impact |
|---|---|---|---|---|
| 1 | SS-02 | `backend/app/main.py:115–133` | Add `Content-Security-Policy` header to `SecurityHeadersMiddleware` | Limits XSS damage even with localStorage tokens |
| 2 | SS-01 | `frontend/src/services/authService.ts` + backend auth routes | Migrate refresh token to `HttpOnly` cookie; keep access token in JS memory only | Prevents refresh token exfiltration via XSS |
| 3 | SS-03 | `backend/app/services/auth_service.py:106–108` | Redis `jti` blocklist on logout — add `jti` claim to access tokens | Closes the 15-minute post-logout access window |
| 4 | SS-04 | `backend/app/models/refresh_token.py` + `auth_service.py` | `session_issued_at` field + 30-day absolute session cap | Limits indefinite session extension |

---

## Checklist Diff

### 1. Session configuration

| Item | Status | Notes |
|---|---|---|
| Secure flag (HTTPS only) | N/A | No cookies — not applicable. On HTTPS, the bearer token is transport-protected by TLS |
| HttpOnly flag (no JS access) | ❌ FAIL | Both tokens stored in `localStorage`, which is fully JS-accessible (SS-01) |
| SameSite attribute | N/A | No cookies — not applicable |
| Session timeout | ✅ PASS | Access: 15 min (`config.py:54`); Refresh: 7 days (`config.py:55`) |
| Absolute session limit | ❌ FAIL | Refresh tokens rotate indefinitely — no maximum session age (SS-04) |
| Session regeneration after login | ✅ PASS | New token pair issued on every login; old refresh tokens are not revoked (parallel sessions allowed — by design) |

### 2. Cookie security

| Item | Status | Notes |
|---|---|---|
| All cookies have appropriate flags | N/A | No cookies set anywhere in the application |
| No sensitive data in cookies | ✅ PASS | No cookies at all |
| Proper domain/path scoping | N/A | No cookies |
| Encryption for sensitive cookies | N/A | No cookies |

### 3. CSRF protection

| Item | Status | Notes |
|---|---|---|
| CSRF token implementation | N/A | Tokens transmitted via `Authorization: Bearer` header, injected by JavaScript — browsers cannot silently attach this header cross-origin. CSRF does not apply to the Bearer token pattern. |
| Double submit cookie pattern | N/A | No cookies — pattern not in use (and not needed) |
| Origin header validation | ✅ PASS | `CORSMiddleware` at `main.py:68–74` enforces an explicit origin list from `CORS_ORIGINS`; wildcard is rejected in production by the validator at `config.py:132–137` |

### 4. Session storage

| Item | Status | Notes |
|---|---|---|
| Not using default in-memory storage in production | ✅ PASS | Refresh tokens stored in PostgreSQL; no in-memory session dict |
| Redis/database backed sessions | ✅ PASS | `refresh_tokens` table in PostgreSQL with `expires_at`, `revoked_at` columns |
| Session cleanup/expiration | ✅ PASS | `expires_at` enforced in `get_valid_by_hash()` query; `revoked_at` set on rotation and logout |

### 5. Additional items evaluated

| Item | Status | Notes |
|---|---|---|
| Content-Security-Policy header | ❌ FAIL | Missing from `SecurityHeadersMiddleware` — primary XSS mitigation absent (SS-02) |
| Refresh token rotation | ✅ PASS | Old token revoked before new one is issued on every `/refresh` call |
| Refresh token theft detection | ✅ PASS | Re-use of a previously rotated token triggers `revoke_all_for_user()` (AUTH-02 in `auth_service.py:92–94`) |
| `token_issued_before` invalidation | ✅ PASS | Password-change events invalidate all prior access tokens (`user.py:25–27`, `deps/auth.py:63–71`) |
| Timing-attack prevention | ✅ PASS | `dummy_verify()` equalises login response time when email not found (`auth_service.py:64`) |
| Access token revocation on logout | ❌ FAIL | Access tokens are stateless JWTs — logout revokes only the refresh token; access token valid 15 more minutes (SS-03) |
| Concurrent session limit | ⚠️ PARTIAL | No active cap; `revoke_all_for_user()` exists for theft response but not as a routine policy (SS-05) |
| Rate limiting on auth endpoints | ✅ PASS | 5/min register, 10/min login, 20/min refresh (enforced by SlowAPI decorators in `routes/auth.py`) |
| Account enumeration prevention | ✅ PASS | Register returns 201 regardless of whether email exists; login uses `dummy_verify()` for timing parity |
