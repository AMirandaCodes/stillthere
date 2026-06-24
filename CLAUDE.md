# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project

**StillThere** — verifies whether a business contact is still employed at a company using publicly available web evidence. React 18 + TypeScript frontend, FastAPI + Python 3.11 backend, Celery workers, PostgreSQL, Redis.

---

## Commands

### Docker (primary development environment)

```bash
docker compose up --build          # start all 6 services (always --build; see note below)
docker compose up --build backend  # rebuild one service only
docker compose restart worker      # Celery workers do NOT hot-reload; must restart after code changes
docker compose exec backend alembic upgrade head   # run migrations after container start
docker compose logs backend --tail 80
docker compose logs worker --tail 80
```

> **OneDrive volume mount caveat:** `./backend:/app` volume mounts are unreliable on OneDrive paths. Always pass `--build` to bake code into the image. Python file changes inside a running container should sync via the mount, but if they don't, rebuild.

### Backend (inside container or venv)

```bash
pytest                                   # all tests
pytest tests/unit/                       # unit tests only (no DB required)
pytest tests/integration/               # requires running PostgreSQL
pytest tests/unit/test_auth.py::test_hash_password   # single test
pytest --cov=app --cov-report=html       # coverage report
alembic upgrade head
alembic revision --autogenerate -m "description"
```

### Frontend (inside container or local node)

```bash
npm run dev          # Vite dev server on :5173
npm run build        # TypeScript compile + Vite bundle
npm run lint         # ESLint (zero warnings enforced)
npm run type-check   # tsc --noEmit
npm test             # Vitest
```

---

## Architecture

### Service topology

```
Browser  →  Vite dev proxy (/api → backend:8000)  →  FastAPI
                                                        ├─ Routes (thin HTTP layer)
                                                        ├─ Services (business logic)
                                                        ├─ Repositories (DB queries)
                                                        └─ Celery task dispatch
                                                              ↓ Redis broker
                                                            Worker
                                                              ├─ Serper.dev (search)
                                                              ├─ httpx + BS4 (scrape)
                                                              └─ Anthropic Claude (analysis)
```

PostgreSQL is the source of truth. Redis serves as the Celery broker, result backend, and HTTP-cache (company profile, search results).

### Request flow — single verification

1. `POST /api/v1/verifications` → `VerificationService.submit()`
2. Service creates **Contact**, **Company**, **Search**, **VerificationResult** (status=PENDING) and commits — so the worker can read immediately.
3. `run_verification.delay(result_id)` is dispatched; HTTP 202 returns `{verification_id}`.
4. Worker runs a 4-stage pipeline: Search → Scrape → LLM analysis → Confidence scoring.
5. Client polls `GET /api/v1/verifications/{id}` every 2 s until `status` is `complete` or `failed`.

### Request flow — batch CSV

1. `POST /api/v1/batch/upload` → `BatchService` parses CSV, pre-creates all DB records (BatchJob + N×JobResult/Search/VerificationResult), dispatches `process_batch_job`.
2. `process_batch_job` fan-outs to N `process_batch_row` tasks (rate-limited at 10/min per worker).
3. Each row task runs the same pipeline as a single verification, then atomically increments BatchJob counters. Sets `status=COMPLETE` when all rows are done.
4. Client polls `GET /api/v1/batch/{id}` every 5 s. Export via `GET /api/v1/batch/{id}/export` returns a CSV blob.

### Layer conventions

| Layer | Location | Rule |
|---|---|---|
| Routes | `app/api/v1/routes/` | HTTP only — no business logic, instantiate service, call one method |
| Services | `app/services/` | Orchestration; own a `session` injected at construction |
| Repositories | `app/repositories/` | All DB queries; extend `BaseRepository[ModelT]` |
| Models | `app/models/` | ORM definitions; all inherit `BaseModel` (UUID PK + timestamps) |
| Schemas | `app/schemas/` | Pydantic request/response; separate from ORM models |
| Tasks | `app/tasks/` | Celery tasks; sync wrapper → `asyncio.run()` → async orchestrator |

---

## Critical non-obvious constraints

### Circular import prevention

`app/db/base.py` defines **only** `Base` (DeclarativeBase). All model imports go in `app/db/registry.py`, which must be imported by `alembic/env.py`, `app/main.py`, and `app/tasks/celery_app.py`. Never import models directly into `base.py`.

### Enum binding — always use `StrEnum` + `values_callable`

All application enums are defined in `app/models/enums.py` using `StrEnum`. SQLAlchemy columns must declare them with:

```python
SAEnum(MyEnum, native_enum=False, length=N, values_callable=lambda x: [e.value for e in x])
```

Without `values_callable`, SQLAlchemy 2.x binds the enum member **name** (`"SINGLE"`) not its value (`"single"`), which fails the VARCHAR CHECK CONSTRAINT. `native_enum=False` avoids PostgreSQL `ALTER TYPE` complexity when adding enum values later.

### NullPool for Celery tasks

`app/db/session.py` exports two session factories:

- `AsyncSessionLocal` — uses QueuePool; for API routes only.
- `TaskSessionLocal` — uses `NullPool`; for all Celery tasks.

Both task files (`verification_tasks.py`, `batch_tasks.py`) import `TaskSessionLocal as AsyncSessionLocal`. **Never use `AsyncSessionLocal` inside a Celery task** — asyncpg connections are bound to the event loop that created them; each `asyncio.run()` call in a task creates a new loop, causing `"Future attached to a different loop"`.

### Celery task idempotency states

Tasks check `VerificationResult.status` on entry:
- `COMPLETE` / `FAILED` → return immediately (safe to re-queue).
- `RUNNING` → delete partial evidence, restart from scratch (crash-recovery).
- `PENDING` → normal first run.

### Database credentials are not renamed

`POSTGRES_USER=cvp_user`, `POSTGRES_PASSWORD=cvp_password`, `POSTGRES_DB=contact_verification` remain in `.env` and `docker-compose.yml` — the live Postgres instance was initialised with these. Changing them requires `docker compose down -v` and full DB recreation.

---

## Environment variables

Copy `.env.example` → `.env`. Minimum required for a working dev stack:

```
ANTHROPIC_API_KEY=sk-ant-...
SERPER_API_KEY=...
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
```

`CORS_ORIGINS` must be a JSON array string: `CORS_ORIGINS=["http://localhost:5173"]`

---

## Frontend proxy

In development the Vite server proxies `/api/*` to the backend. The proxy target comes from `VITE_API_BASE_URL` (set to `http://backend:8000` in `docker-compose.yml` frontend service environment). `axios` in the browser always uses `baseURL: "/api"` — never a direct backend URL. Do not set `VITE_API_BASE_URL` to anything the browser can reach directly; it is only read server-side by `vite.config.ts`.

localStorage keys for auth tokens are `stillthere_access_token` and `stillthere_refresh_token`.
