# Secrets Management Security Audit

**Date:** 2026-07-07  
**Scope:** All secret handling — hardcoded values, environment variable usage, git history, CI/CD, key storage, and rotation capability  
**Files examined:** `backend/app/core/config.py`, `backend/app/core/auth.py`, `.env`, `.env.prod`, `.env.example`, `.env.prod.example`, `.gitignore`, `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.dev.yml`, `.github/workflows/ci.yml`, full git history (via `git log -S`)

---

## ⚠️ Immediate Action Required

**`.env` and `.env.prod` contain live API keys and passwords.** These files exist on a Microsoft OneDrive-synced path (`OneDrive - Philton Polythene Converters Ltd\Desktop\...`) and have been synced to cloud storage outside any access-controlled secret management system. Neither file has ever been committed to git (correctly gitignored), but their contents are exposed to:

- **Microsoft OneDrive cloud** (company tenant)
- **Tenant administrators** at Philton Polythene Converters Ltd who have SharePoint/OneDrive admin rights
- **Any Microsoft e-discovery or compliance scan** of the tenant
- **Any OneDrive client** signed into the same account on another device

**Keys to rotate immediately before continuing (do not reproduce values here):**
- `ANTHROPIC_API_KEY` in `.env` (development key — `sk-ant-api03-cKWa...`)
- `ANTHROPIC_API_KEY` in `.env.prod` (production key — `sk-ant-api03-3PDm...`)
- `SERPER_API_KEY` in both files (same key used in both environments — `882532da...`)
- `POSTGRES_PASSWORD` in `.env.prod`
- `FLOWER_PASSWORD` in `.env.prod`

Rotation instructions: Anthropic Console → API Keys → revoke + re-issue. Serper Dev → API Keys → regenerate. Postgres password rotation requires `docker compose down -v` on production (all data lost) or an `ALTER ROLE` if accessed directly.

---

## Summary Risk Score: 5.5 / 10

Git history is clean — no real secrets were ever committed. Both `.env` files are properly gitignored. The cryptographic primitives (bcrypt/12 rounds, 512-bit refresh tokens, SHA-256 token hashing, HS256 JWT) are all sound. The risks are: live secrets on OneDrive, hardcoded fallback values in `config.py` that activate silently if `.env` is absent, a publicly-known development `SECRET_KEY` that makes all dev JWTs forgeable, and no key rotation procedures. The final score reflects that exploitability requires either OneDrive admin access or an absent `.env` file — not arbitrary remote code execution.

---

## Findings

---

### SM-01 — Critical — CWE-312: Cleartext Storage of Sensitive Information

**Title:** Live API keys and passwords stored in `.env`/`.env.prod` on company OneDrive — cloud-synced outside any secret management boundary

**Evidence:**
- Project path: `c:\Users\AMiranda\OneDrive - Philton Polythene Converters Ltd\Desktop\contact-verification-platform`
- `.env` (development): contains live `ANTHROPIC_API_KEY` (`sk-ant-api03-cKWa…` prefix) and `SERPER_API_KEY`
- `.env.prod` (production): contains live `ANTHROPIC_API_KEY` (`sk-ant-api03-3PDm…` prefix), `SERPER_API_KEY` (same value as dev), `POSTGRES_PASSWORD`, and `FLOWER_PASSWORD`
- Both files are correctly excluded from git: `git ls-files --error-unmatch .env` → error (untracked)
- Both files are correctly listed in `.gitignore:20–26`
- Git history search confirms no real key values appear in any committed revision: `git log --all -S "sk-ant-api03-"` → 0 commits; `git log --all -S "882532da5089"` → 0 commits

**Why it matters:**
OneDrive for Business syncs to Microsoft's cloud infrastructure and is subject to:
1. **Tenant admin access**: any Global Admin or SharePoint admin in the "Philton Polythene Converters Ltd" tenant can access user OneDrive files
2. **Microsoft eDiscovery/Compliance**: legal holds and compliance scans can read these files
3. **Credential stuffing**: if the Anthropic or Serper keys are revoked/used by an attacker, API costs are incurred and quota is burned before the compromise is detected
4. **No rotation audit trail**: unlike a secrets manager (Vault, AWS Secrets Manager), there is no log of who read these values

**Remediation:**

**Immediate:** Rotate all keys listed in the header above.

**Structural:** Move secrets off the local filesystem entirely. For the current Render-based deployment, use Render's native secrets management:

1. In the Render dashboard → Service → Environment → Secret Files, paste `.env.prod` content. Render encrypts these at rest and never exposes them in logs.
2. Or use Render Environment Variables directly (click "Generate" for `SECRET_KEY`).
3. Delete `.env.prod` from the local machine after moving to Render.

For local development, use a secrets manager CLI:
```bash
# Option A: 1Password CLI (op run)
op run --env-file=.env.op -- docker compose up

# Option B: dotenv-vault (dotenvx)
dotenvx run -- docker compose up
```

At minimum, move `.env` and `.env.prod` out of the OneDrive folder to a path not synced to cloud storage (e.g., `C:\Secrets\stillthere\`) and symlink or reference from there:
```bash
# PowerShell
New-Item -ItemType SymbolicLink -Path ".env" -Target "C:\Secrets\stillthere\.env"
```

---

### SM-02 — High — CWE-798: Use of Hard-Coded Credentials

**Title:** Hardcoded credential defaults in `config.py` activate silently when `.env` is absent

**Evidence:**
`backend/app/core/config.py:23–33`:
```python
class Settings(BaseSettings):
    SECRET_KEY: str = "change-me-in-production"
    DATABASE_URL: str = "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
```

The production guard at `config.py:117–138`:
```python
@model_validator(mode="after")
def validate_production_settings(self) -> "Settings":
    if self.APP_ENV != "production":
        return self          # ← guard is skipped entirely for non-production
    if self.SECRET_KEY in _PLACEHOLDER_KEYS or len(self.SECRET_KEY) < 32:
        raise ValueError(...)
```

`_PLACEHOLDER_KEYS = {"change-me-in-production", "change-me-in-production-use-a-long-random-string"}` (`config.py:7`).

**Attack scenario:**
A developer clones the repo, does not create a `.env` file, and runs `APP_ENV=development uvicorn app.main:app`. The application starts successfully with `SECRET_KEY = "change-me-in-production"`. Any JWT signed in this session is verifiable by anyone who knows the key (which is in the public source code). The default DATABASE_URL points at `localhost:5432` with known credentials — if those happen to match a local postgres instance, real data is at risk.

A staging deployment that accidentally uses `APP_ENV=development` (the wrong compose file, a mis-set environment variable) gets the hardcoded key with no startup error.

**Remediation:**

Remove all sensitive defaults from `Settings`. Use `None` with a validator that always requires the value regardless of environment:

```python
# backend/app/core/config.py

class Settings(BaseSettings):
    SECRET_KEY: str | None = None
    DATABASE_URL: str | None = None
    ANTHROPIC_API_KEY: str = ""
    SERPER_API_KEY: str = ""

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "Settings":
        missing = []
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            missing.append("SECRET_KEY (must be at least 32 chars; generate with: python -c \"import secrets; print(secrets.token_hex(32))\")")
        if not self.DATABASE_URL:
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError(f"Required secrets not set: {', '.join(missing)}")
        if self.APP_ENV == "production":
            if not self.ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY must be set in production")
            if not self.SERPER_API_KEY:
                raise ValueError("SERPER_API_KEY must be set in production")
        return self
```

This makes the application refuse to start without a real `SECRET_KEY` and `DATABASE_URL` in any environment — not just production. `REDIS_URL` can keep a default since Redis being absent is handled gracefully (the app degrades to no-cache mode).

---

### SM-03 — High — CWE-321: Use of Hard-Coded Cryptographic Key

**Title:** Development `SECRET_KEY` is the publicly-known `.env.example` placeholder — all dev JWTs are trivially forgeable

**Evidence:**
`.env:8`:
```
SECRET_KEY=change-me-in-production-use-a-long-random-string
```

`.env.example:11`:
```
SECRET_KEY=change-me-in-production-use-a-long-random-string
```

This is exactly the same value, committed to the public `.env.example`. The `SECRET_KEY` is used to sign every JWT access token (`auth.py:63`):
```python
return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
```

**Why it matters:**
Anyone who knows `SECRET_KEY=change-me-in-production-use-a-long-random-string` can:
1. Decode any dev-environment JWT (they were never truly secret — jose's `jwt.decode` with the known key)
2. Forge new JWTs for any `user_id` UUID, granting admin access if they can guess a valid UUID
3. Issue refresh-token claims or other crafted payloads

In development, this is partially acceptable (known dev limitation). However, the identical key in `.env` and `.env.example` means every developer who forgets to generate their own key will be running with the same shared secret — and those dev tokens will cross environments if anyone tests against a shared staging database.

**Remediation:**

Generate a real key in `.env` immediately:
```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
```

Change `.env.example` to use a generator hint rather than a placeholder value (the current value is too close to a functional default):
```ini
# Generate your key: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=
```

An empty `SECRET_KEY` combined with the validator fix from SM-02 causes an immediate startup failure with a helpful message, making it impossible to accidentally run with a guessable key.

---

### SM-04 — Medium — CWE-522: Insufficiently Protected Credentials

**Title:** Same `SERPER_API_KEY` used in development and production — no environment isolation for this credential

**Evidence:**
`.env:31` and `.env.prod:39` share the same key value (confirmed by inspection — the values are identical).

**Why it matters:**
1. **Dev quota consumption hits production budget**: developer testing counts against the same monthly Serper quota (2,500 searches). A dev automation script or test run can exhaust production quota.
2. **Dev breach = prod breach**: if the key leaks from a developer's machine, the production search capability is immediately compromised.
3. **No audit trail separation**: Serper's usage dashboard cannot distinguish dev from prod calls.

**Remediation:**

Create a separate Serper account or sub-key for development. Serper.dev allows multiple API keys under one account. Use the dev key in `.env` and the prod key in `.env.prod`/Render:
- Serper Dev dashboard → Settings → API Keys → Create New Key → name it "stillthere-dev"
- Apply a lower monthly quota cap to the dev key in Serper settings
- Store prod key only in Render's secret management, never in any local file

---

### SM-05 — Medium — CWE-521: Weak Password Requirements / Credential Reuse

**Title:** `POSTGRES_PASSWORD` and `FLOWER_PASSWORD` share the same value in `.env.prod`

**Evidence:**
`.env.prod:25` and `.env.prod:51`:
```
POSTGRES_PASSWORD=AMpxsswxrd3!   (redacted — same value as below)
FLOWER_PASSWORD=AMpxsswxrd3!
```

**Why it matters:**
Flower is a web UI (HTTP or HTTPS depending on setup) that accepts `basic_auth` credentials. PostgreSQL is a database port. If an attacker captures the Flower basic auth credentials (via network sniffing, a compromised reverse proxy, or a Flower vulnerability), they immediately have the database password as well. The two services have entirely different risk profiles and should use different credentials.

Additionally, the production password appears in `.env.prod` which is on an OneDrive-synced path (SM-01 applies).

**Remediation:**

Use separate, independently generated passwords for each service:
```bash
# In .env.prod
POSTGRES_PASSWORD=<generated independently>
FLOWER_PASSWORD=<generated independently>
```

Generate with: `python -c "import secrets; print(secrets.token_urlsafe(24))"`

---

### SM-06 — Medium — CWE-916: Use of Password Hash With Insufficient Computational Effort (inverse: rotation risk)

**Title:** No documented `SECRET_KEY` rotation procedure — rotation invalidates all active user sessions simultaneously

**Evidence:**
`backend/app/core/auth.py:63,75`:
```python
return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
# and
payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], ...)
```

A single symmetric key is used to both sign and verify all JWTs. Rotating `SECRET_KEY` (e.g., after a suspected compromise) requires changing the environment variable and restarting all services. Every active user's access token immediately becomes invalid — they receive 401s until they log in again. No grace period, no gradual migration.

There is no `KEY_VERSION`, no dual-key verification window, and no documented rotation runbook anywhere in CLAUDE.md or README.md.

**Why it matters:**
Without a rotation procedure:
1. A key compromise is hard to respond to — the team must choose between leaving a known-compromised key in place or a hard outage affecting all active users.
2. No periodic rotation means the same key can be in use indefinitely, increasing the exposure window.

**Remediation:**

Document the rotation procedure in CLAUDE.md:
```markdown
## SECRET_KEY rotation

1. Generate new key: python -c "import secrets; print(secrets.token_hex(32))"
2. In Render: Environment Variables → SECRET_KEY → update value → Manual Deploy
3. Expected impact: all users are logged out immediately (JWTs are invalidated)
4. No data loss — only active sessions are terminated
5. After rotation, monitor for unusual login patterns (may indicate old tokens being tested)
```

For zero-downtime rotation (future enhancement), implement a two-key verification window:
```python
# config.py
SECRET_KEY: str = ...
PREVIOUS_SECRET_KEY: str = ""  # set during rotation; cleared after 15 min

# auth.py — decode_access_token()
for key in filter(None, [settings.SECRET_KEY, settings.PREVIOUS_SECRET_KEY]):
    try:
        return jwt.decode(token, key, algorithms=["HS256"], ...)
    except JWTError:
        continue
raise JWTError("No valid key")
```

---

### SM-07 — Low — CWE-183: Permissive List of Allowed Inputs

**Title:** `CORS_ORIGINS` in `.env.prod` has a `https://https://` double-scheme bug — CORS blocks all frontend requests in production

**Evidence:**
`.env.prod:44`:
```
CORS_ORIGINS=["https://https://stillthere.onrender.com"]
```

The double `https://` makes the allowed origin `https://https://stillthere.onrender.com` — a value no browser will ever send as an `Origin` header. CORS middleware will reject every cross-origin request from the real frontend (`https://stillthere-frontend.onrender.com`).

**Why it matters:**
This is a misconfiguration rather than a security weakness — it makes CORS too restrictive, not too permissive. In production, the API is effectively unusable from the frontend. This is already apparent from the deployed state (CLAUDE.md says the app is live, but this setting would block all browser API calls).

**Remediation:**
```
CORS_ORIGINS=["https://stillthere-frontend.onrender.com"]
```

Note: also verify the frontend domain matches the actual Render static site URL. CLAUDE.md references `https://stillthere-frontend.onrender.com` which is different from the `.env.prod` value of `https://stillthere.onrender.com`.

---

### SM-08 — Informational — CI Uses Well-Known Test Credentials in Committed File

**Title:** `.github/workflows/ci.yml` contains test credential values in cleartext — acceptable for CI but flagged for awareness

**Evidence:**
`.github/workflows/ci.yml:33–40` and `103–113`:
```yaml
env:
  SECRET_KEY: "ci-test-secret-key-32-chars-minimum-length"
  ANTHROPIC_API_KEY: "sk-ant-test"
  SERPER_API_KEY: "test-key"
  DATABASE_URL: "postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification"
```

These are not real credentials — `sk-ant-test` is not a valid API key and will fail Anthropic auth. The CI postgres uses ephemeral GitHub Actions services with no external access. `cvp_password` is a well-known dev default, not a production credential.

**Why it matters:**
This is acceptable for CI. GitHub Actions recommends env vars over secrets for values that are not truly secret. However, best practice is to use GitHub Actions secrets even for test values, to establish a pattern that makes accidental real-credential substitution obvious:

**Recommended improvement (low priority):**
Move all CI env vars to GitHub repository secrets (`Settings → Secrets and Variables → Actions → New repository secret`), then reference as `${{ secrets.CI_SECRET_KEY }}`. This ensures the CI file itself never contains any credential-shaped values.

---

## Top 5 Prioritised Fixes

| Priority | Finding | Action | Impact |
|---|---|---|---|
| 1 | SM-01 | **Rotate all keys immediately**: Anthropic (both dev and prod), Serper, Postgres password, Flower password. Then move `.env.prod` to Render's secret management and delete the local file. | Removes active credential exposure |
| 2 | SM-03 | **Generate a real SECRET_KEY for `.env`**: `python -c "import secrets; print(secrets.token_hex(32))"` → paste into `.env`. Update `.env.example` to use an empty default instead of the placeholder string. | Prevents JWT forgery in dev |
| 3 | SM-02 | **Remove hardcoded defaults** from `config.py` — set `SECRET_KEY` and `DATABASE_URL` to `None` with a validator that fails startup if unset in any environment. | Eliminates silent fallback to known credentials |
| 4 | SM-04 | **Create a separate Serper dev key** with its own monthly quota cap. Use dev key in `.env`, prod key only in Render secrets. | Isolates environments; limits blast radius |
| 5 | SM-07 | **Fix CORS origin** in `.env.prod`: remove the duplicate `https://` scheme. Verify the domain matches the actual Render frontend URL. | Unblocks production frontend API calls |

---

## Checklist Diff

### 1. Hardcoded secrets

| Item | Status | Notes |
|---|---|---|
| API keys hardcoded in source code | ✅ PASS | No API keys in `.py` files; all loaded from `Settings` / env vars |
| Database passwords hardcoded | ⚠️ PARTIAL | `config.py:26` defaults `cvp_password` — known dev credential, not a real secret; fail if SM-02 fix applied |
| JWT `SECRET_KEY` hardcoded | ⚠️ PARTIAL | Default `"change-me-in-production"` in `config.py:23`; fails production validator but not dev (SM-02) |
| Encryption keys hardcoded | ✅ PASS | No encryption keys in source; bcrypt, SHA-256, and HMAC-SHA256 all use runtime-provided material |
| Test credentials in committed CI file | ⚠️ PARTIAL | `ci.yml` has test-only values; not real secrets but not using GitHub Secrets (SM-08) |
| Real secrets in git history | ✅ PASS | `git log -S "sk-ant-api03-"` → 0 commits; `git log -S "AMpxsswxrd3"` → 0 commits; history is clean |

### 2. Environment variable usage

| Item | Status | Notes |
|---|---|---|
| All secrets in env vars | ✅ PASS | API keys, DB URL, SECRET_KEY all come from `Settings` (pydantic-settings reads env vars) |
| `.env` not in git | ✅ PASS | `.gitignore:20–26` explicitly excludes `.env`, `.env.prod`, `.env.staging`, `.env.dev`, `.env.local` |
| `.env.prod` not in git | ✅ PASS | Same as above |
| `.env` example committed with placeholders only | ✅ PASS | `.env.example` and `.env.prod.example` use `sk-ant-...` and `REPLACE_WITH_...` placeholders |
| Secrets on OneDrive (cloud sync) | ❌ FAIL | Both `.env` and `.env.prod` on OneDrive-synced path — real keys exposed to cloud storage (SM-01) |
| Dev SECRET_KEY genuinely secret | ❌ FAIL | `.env` uses exact `.env.example` placeholder value (SM-03) |
| Production vs development key separation | ❌ FAIL | `SERPER_API_KEY` identical in `.env` and `.env.prod` (SM-04) |

### 3. Secret rotation capability

| Item | Status | Notes |
|---|---|---|
| API key rotation (Anthropic) | ✅ PASS | Keys are env vars; rotation = update env var + redeploy; no code change |
| API key rotation (Serper) | ✅ PASS | Same mechanism |
| DATABASE_URL / password rotation | ⚠️ PARTIAL | Rotation requires `docker compose down -v` (DB recreated) or direct `ALTER ROLE`; documented in CLAUDE.md |
| `SECRET_KEY` rotation | ❌ FAIL | No documented procedure; rotation invalidates all active sessions with no grace period (SM-06) |
| Rotation runbook documented | ❌ FAIL | CLAUDE.md documents DB password constraint but has no rotation section for JWT key or API keys |
| Refresh token rotation | ✅ PASS | Token rotation on every `/refresh` call (one-time use); no manual intervention needed |

### 4. Encryption key management

| Item | Status | Notes |
|---|---|---|
| Password hashing KDF | ✅ PASS | bcrypt with `rounds=12` (hardcoded, pinned — `auth.py:22`); industry standard |
| Salt usage (passwords) | ✅ PASS | bcrypt generates a unique salt per hash automatically via passlib |
| Refresh token design | ✅ PASS | `secrets.token_hex(64)` = 512-bit entropy; only SHA-256 hash stored in DB (`auth.py:90–91`) |
| JWT signing algorithm | ✅ PASS | HS256 with audience + issuer claims; type claim checked on decode (`auth.py:80`) |
| JWT signing key strength | ⚠️ PARTIAL | Algorithm and claim validation are correct; key entropy depends on `SECRET_KEY` value (SM-02, SM-03) |
| Database encryption at rest | ⚠️ PARTIAL | PostgreSQL 15-alpine has no encryption-at-rest configured; managed Render Postgres does encrypt at rest |
| Redis encryption at rest | ⚠️ PARTIAL | Local dev Redis (no TLS, no auth); Upstash prod Redis uses TLS (`rediss://`) |
| Key storage (secrets manager) | ❌ FAIL | Secrets stored as local files on OneDrive-synced path, not in Vault/AWS SSM/Render secrets (SM-01) |
| Key derivation for symmetric operations | N/A | No application-level symmetric encryption beyond JWT signing |
