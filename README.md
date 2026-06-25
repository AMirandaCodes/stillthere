# StillThere

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.5-3178C6?style=flat-square&logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)

> Automatically verifies whether a business contact is still employed at a company — backed by cited web evidence, never by inference.

---

## Table of Contents

- [What This Project Solves](#what-this-project-solves)
- [Engineering Highlights](#engineering-highlights)
- [Architecture](#architecture)
- [Verification Pipeline](#verification-pipeline)
- [Key Design Decisions](#key-design-decisions)
- [Screenshots](#screenshots)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Development](#local-development)
- [Environment Variables](#environment-variables)
- [API Documentation](#api-documentation)
- [Running Tests](#running-tests)
- [Development Phases](#development-phases)

---

## What This Project Solves

Sales and recruitment teams lose significant time reaching out to stale contacts. When a prospect has changed roles or left a company, outbound messages bounce or land with the wrong person — damaging response rates and wasting pipeline. Manually checking LinkedIn for dozens or hundreds of contacts is slow and inconsistent at scale.

**StillThere** automates this verification using publicly available web evidence. Given a person's name, company, and optional email, the system runs a four-stage pipeline: it queries the web through the Serper API, scrapes the most relevant pages, passes the collected text to the Claude API for structured extraction, and scores the results deterministically. The output is five independently sourced binary signals — *Person Found Online*, *Associated With Company*, *Found on Company Website*, *Company Active*, *Email Match* — each backed by a cited URL and the model's reasoning.

The design is deliberately conservative: the system returns **Unclear** when evidence is insufficient rather than guessing. Every result is auditable because every conclusion traces back to a specific web page.

The platform supports two workflows:

| Workflow | Description |
|---|---|
| **Single Verification** | Enter a name, company, and optional email → receive an evidence-backed report within 20–90 seconds |
| **Batch CSV Upload** | Upload a CSV of contacts → background processing with a live progress dashboard and CSV export |

---

## Engineering Highlights

- **Async pipeline decoupled from HTTP** — Celery workers process each verification in the background; the API returns HTTP 202 immediately with a job ID. The client polls until complete. This decouples response latency from pipeline runtime and enables batch fan-out to N parallel tasks.

- **Event loop isolation in Celery workers** — Celery tasks each call `asyncio.run()`, creating a new event loop per execution. asyncpg connections from a shared pool are bound to the loop that created them, causing `"Future attached to a different loop"` errors at scale. The fix: a dedicated `NullPool` SQLAlchemy engine used exclusively by task code, ensuring fresh connections per task invocation.

- **LLM-structured evidence extraction** — Claude receives the raw text of each scraped page and returns a structured JSON object identifying which of the five evidence signals the page supports, with verbatim quotes and reasoning per signal. This handles the variety of web page formats that rules-based extraction cannot.

- **Evidence-first result model** — Five independent tri-state signals (Yes / No / Unclear). The confidence score is computed deterministically from field determination and source quality — no ML inference in the scoring layer. Unclear signals do not count as evidence either way.

- **Batch CSV fan-out with atomic progress** — A batch job pre-creates all database records before dispatching N Celery tasks. Each row task atomically increments shared counters on the parent job. Progress is visible in real time via polling.

- **Rotate-on-use refresh tokens** — Refresh tokens are single-use; re-using a spent token returns 401. This prevents replay attacks without requiring a server-side token blocklist.

- **SQLAlchemy 2.x enum binding** — SQLAlchemy 2.x with `native_enum=False` binds enum `.name` (uppercase) by default, not `.value`. Database check constraints were created with lowercase values. The non-obvious fix — `values_callable=lambda x: [e.value for e in x]` on every `SAEnum` declaration — must be applied to every enum column and fails silently at definition time; the error only surfaces as a `CheckViolationError` at insert time.

- **Savepoint-based test isolation** — Integration tests run inside a connection-level transaction. `session.commit()` calls create savepoints (via SQLAlchemy 2.x `join_transaction_mode="create_savepoint"`) rather than committing the outer transaction. `connection.rollback()` at teardown undoes all changes without any DDL between tests.

---

## Architecture

```
Browser  →  Vite dev proxy (/api → backend:8000)  →  FastAPI
                                                        ├─ Routes        (HTTP only — no logic)
                                                        ├─ Services      (business logic, own the session)
                                                        ├─ Repositories  (all DB queries)
                                                        └─ Celery dispatch
                                                                ↓ Redis broker
                                                             Worker
                                                                ├─ Serper.dev  (web search)
                                                                ├─ httpx + BS4 (page scraping)
                                                                └─ Claude API  (evidence extraction)
                                                                        ↓
PostgreSQL  ←───────────────── single source of truth ──────────────────┘
     Redis  ←───────────────── ephemeral only (broker, cache, results)
```

The application is a **layered monolith**. Routes are kept intentionally thin — they validate the incoming request, instantiate a service, call one method, and return the response. All business logic lives in the service layer, which owns the database session and coordinates across multiple repositories within a single transaction. Repositories perform all SQL queries; nothing else touches SQLAlchemy directly.

Celery sits alongside FastAPI rather than inside it. When a route dispatches a verification task, it writes a `PENDING` result row to PostgreSQL and returns HTTP 202 immediately. The worker picks up the job from Redis, runs the four-stage pipeline, and writes results back to PostgreSQL. The client discovers completion by polling `GET /api/v1/verifications/{id}` every two seconds.

**PostgreSQL is the single source of truth.** Redis is strictly ephemeral: Celery broker, result backend, and HTTP cache only. Losing Redis does not lose data — the API falls back gracefully if Redis is unavailable.

---

## Verification Pipeline

Each verification runs a deterministic four-stage pipeline inside a Celery worker. The pipeline is idempotent: if a task crashes mid-run and is re-queued, it detects the partial state, discards any incomplete evidence, and restarts cleanly.

### Stage 1 — Search

`SearchService` fires 3–4 concurrent queries through the Serper API (programmatic Google results):

| Query type | Purpose |
|---|---|
| `"Name" "Company"` | Broad person-company association |
| `site:linkedin.com "Name" "Company"` | LinkedIn profile presence |
| `"Company" official site` | Company homepage and trading status |
| `"email@domain"` *(optional)* | Email address presence check |

Results from all queries are merged and deduplicated by URL. The raw Serper JSON is stored on the `Search` record for audit purposes.

### Stage 2 — Scrape

`EvidenceService` fetches each unique URL asynchronously using `httpx`. Pages are parsed with BeautifulSoup4 to extract clean body text, stripping navigation, scripts, and advertisements. Up to 20 sources are collected per verification. Each source is classified by type (`professional_profile`, `company_website`, `business_directory`, `search_result`, `other`) based on domain pattern matching — this classification feeds directly into the confidence score.

### Stage 3 — LLM Analysis

Each page's text is sent to the Claude API with a structured prompt. Claude is asked to identify which of the five evidence signals the page supports, to quote the specific text that led to each conclusion, and to return a structured JSON object. The service parses and validates the JSON, discarding malformed responses.

Claude is explicitly instructed to return `unclear` for any signal where the page is ambiguous or silent — it is never asked to infer or guess. This uncertainty propagates directly to the result.

### Stage 4 — Confidence Scoring

`ConfidenceService` computes a 0–100 score from two independent components:

**Field determination (0–50 pts):** 10 points for each of the five signals that has a definite outcome (`yes` or `no`). A signal left as `unclear` contributes 0. This rewards breadth of evidence coverage.

**Source quality (0–50 pts, capped):** a sum of per-source weights based on the source type assigned in Stage 2:

| Source type | Weight |
|---|---|
| Professional profile (LinkedIn, Xing…) | 12 |
| Company website | 10 |
| Business directory | 7 |
| Search result | 5 |
| Other | 3 |

The total maps to a confidence level: **High** (≥70), **Medium** (40–69), **Low** (<40). No machine learning is involved — the algorithm is fully deterministic and auditable.

---

## Key Design Decisions

| Decision | Alternative Considered | Reason for This Choice |
|---|---|---|
| **Celery for async verification** | Synchronous endpoint with long-polling | Avoids HTTP gateway timeouts (pipeline runs 20–90 s). Enables batch fan-out to N parallel tasks without blocking the API server. |
| **Serper.dev for web search** | Scraping Google search results directly | Structured JSON API with predictable rate limits. Avoids Google's Terms of Service restrictions on automated scraping. 2,500 free searches per month covers the target use case. |
| **Claude API for evidence extraction** | Regex / CSS-selector heuristics | Unstructured web text resists rules-based parsing — page layouts, copy, and structure vary too widely. The LLM handles variation naturally and can express uncertainty (`unclear`) when a page is ambiguous, which a regex cannot. |
| **NullPool for Celery workers** | Shared `QueuePool` (same engine as the API) | Each `asyncio.run()` call in a Celery task creates a new event loop. asyncpg connections are bound to the loop that created them. Reusing pooled connections across different loops raises `"Future attached to a different loop"` at runtime. `NullPool` creates a fresh connection per task invocation, eliminating the loop mismatch entirely. |
| **Five independent tri-state signals** | Single composite confidence percentage | Individual signals are auditable and actionable. A user can see *why* a result is uncertain — the person was found online but not on the company website — rather than receiving a percentage with no explanation. |
| **Layered monolith over microservices** | Separate services for search, scraping, and analysis | Simpler deployment, single transactional boundary, and explicit layer contracts. The separation could be extracted to services later with minimal refactoring. |
| **`values_callable` on all SQLAlchemy enums** | Default `SAEnum` with `native_enum=False` | SQLAlchemy 2.x binds enum `.name` (e.g. `"PENDING"`) rather than `.value` (e.g. `"pending"`). Database check constraints use lowercase values. Without `values_callable`, every enum insert raises a `CheckViolationError` that is invisible until that code path is exercised. |
| **Savepoint-based test isolation** | Truncating tables between tests | Each integration test runs inside a connection-level transaction. `session.commit()` calls produce savepoints, not real commits. `connection.rollback()` at teardown undoes everything with no DDL overhead between tests. |

---

## Screenshots

> *The application is running in development mode. Production deployment (Phase 7) is not yet complete.*

**Login**
> *[Screenshot — Centred card with "StillThere" heading, email and password fields, a "Sign in" button, and a "Create account" link below. Clean light background.]*

**Single Verification — Submit**
> *[Screenshot — Home page with a three-field form: Full Name (required), Company (required), Work Email (optional). A blue "Run Verification" primary button submits the request.]*

**Verification Result**
> *[Screenshot — Result page with a header card showing name, company, and submission date. Below: five tri-state badge rows labelled Person Found Online, Appears Associated With Company, Found on Company Website, Company Active / Still Trading, Email Match — each showing a green "Yes", red "No", or grey "Unclear" pill. A confidence bar displays the score (e.g. 74 / 100 — High) with colour coding. An evidence sources table lists each URL with its title, source type, collection date, and Claude's reasoning for the signal it supports.]*

**Batch Jobs Dashboard**
> *[Screenshot — Jobs page listing upload cards. Each card shows the CSV filename, a coloured status badge (Queued / Running / Complete / Failed), a labelled progress bar (e.g. "14 / 20 rows — 70%"), and three count chips (ok / failed / unclear). An "Export CSV" button becomes active when the job status reaches Complete.]*

---

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Data fetching | TanStack Query (React Query) |
| Forms | React Hook Form + Zod |
| Backend | Python 3.11, FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Task queue | Celery + Redis |
| Task monitor | Flower |
| Database | PostgreSQL 15 |
| Web search | Serper.dev API |
| AI analysis | Anthropic Claude API |
| HTTP client | httpx (async) |
| HTML parsing | BeautifulSoup4 |
| Rate limiting | slowapi |
| Logging | structlog (structured JSON) |
| Containers | Docker + Docker Compose |
| Testing | pytest, pytest-asyncio, Vitest, Testing Library |
| CI | GitHub Actions |

---

## Project Structure

```
stillthere/
├── .github/
│   └── workflows/
│       └── ci.yml                   # Unit + integration + frontend CI (three parallel jobs)
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/           # HTTP handlers — no business logic, one service call each
│   │   ├── core/                    # Config (pydantic-settings), logging, security helpers
│   │   ├── db/                      # Engine, session factories (API + Task), model registry
│   │   ├── models/                  # SQLAlchemy ORM definitions + StrEnum types
│   │   ├── repositories/            # All DB queries — one class per entity
│   │   ├── schemas/                 # Pydantic request / response models
│   │   ├── services/                # SearchService, EvidenceService, LLMService,
│   │   │                            #   ConfidenceService, BatchService, CacheService, …
│   │   ├── tasks/                   # Celery app + verification_tasks + batch_tasks
│   │   └── main.py                  # FastAPI factory, lifespan (Redis, CORS, rate limiting)
│   ├── alembic/                     # Database migrations
│   ├── docker/
│   │   └── init-test-db.sql         # Creates contact_verification_test on first boot
│   ├── tests/
│   │   ├── conftest.py              # Fixtures — savepoint isolation, HTTP client, auth headers
│   │   ├── unit/                    # 9 files — no DB; services tested with mocked I/O
│   │   └── integration/             # 9 files — full HTTP stack + real DB via test client
│   ├── .coveragerc
│   ├── pytest.ini
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── components/              # Spinner, TriStateBadge, StatusBadge, ConfidenceScore,
│       │                            #   Pagination, ProtectedRoute, Layout
│       ├── context/                 # AuthContext — session restore, login, logout
│       ├── pages/                   # Login, Register, Home, VerificationResult,
│       │                            #   SearchHistory, BatchUpload, BatchJobs
│       ├── services/                # api.ts (axios + interceptors), authService,
│       │                            #   verificationService, batchService
│       ├── types/                   # TypeScript interfaces matching backend Pydantic schemas
│       └── test-setup.ts            # Vitest + @testing-library/jest-dom bootstrap
│   ├── vitest.config.ts
│   ├── package.json
│   └── Dockerfile
├── CLAUDE.md                        # AI assistant guidance (commands, architecture, constraints)
├── docker-compose.yml               # Full development stack — 6 services
├── docker-compose.dev.yml           # Dev overrides — hot-reload, debug logging
└── .env.example
```

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/your-username/stillthere.git
cd stillthere

# 2. Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and SERPER_API_KEY at minimum

# 3. Start all services
docker compose up --build

# 4. Run database migrations
docker compose exec backend alembic upgrade head

# Services:
#   Frontend:        http://localhost:5173
#   API + Swagger:   http://localhost:8000/api/docs
#   Flower (tasks):  http://localhost:5555
```

> **OneDrive note:** Volume mounts (`./backend:/app`) are unreliable on OneDrive paths. Always pass `--build` so code changes are baked into the image.

The `contact_verification_test` database is created automatically when the PostgreSQL volume is first initialised. If you already have an existing volume, create it manually:

```bash
docker compose exec db psql -U cvp_user -c "CREATE DATABASE contact_verification_test;"
```

---

## Local Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

docker compose up db redis -d   # start only the dependencies

alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev   # Vite dev server on :5173, proxies /api to localhost:8000
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key — [console.anthropic.com](https://console.anthropic.com) |
| `SERPER_API_KEY` | Yes | Google Search API key — [serper.dev](https://serper.dev) (2,500 free/month) |
| `SECRET_KEY` | Yes | JWT signing secret — minimum 32 characters. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_USER` | Yes | PostgreSQL username (default: `cvp_user`) |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `POSTGRES_DB` | Yes | Main database name (default: `contact_verification`) |
| `REDIS_URL` | Yes | Redis connection string (default: `redis://redis:6379/0`) |
| `CORS_ORIGINS` | No | **JSON array string**: `["http://localhost:5173"]` — not comma-separated |
| `DEBUG` | No | `true` enables verbose logging and auto-migration on startup |

`DATABASE_URL` is composed automatically from the `POSTGRES_*` variables inside `docker-compose.yml`. Do not set it manually when using Docker Compose.

See [`.env.example`](.env.example) for all available variables and defaults.

---

## API Documentation

FastAPI generates interactive documentation automatically from route and schema definitions:

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

### Core Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Create account |
| `POST` | `/api/v1/auth/login` | Obtain access + refresh tokens |
| `POST` | `/api/v1/auth/refresh` | Rotate refresh token (single-use) |
| `GET` | `/api/v1/auth/me` | Current user |
| `POST` | `/api/v1/verifications` | Submit a new verification |
| `GET` | `/api/v1/verifications/{id}` | Poll verification result |
| `GET` | `/api/v1/verifications` | List verifications (paginated) |
| `POST` | `/api/v1/batch/upload` | Upload CSV for batch processing |
| `GET` | `/api/v1/batch/{id}` | Poll batch job status |
| `GET` | `/api/v1/batch/{id}/export` | Download results as CSV |
| `GET` | `/api/v1/health` | Health check |

---

## Running Tests

```bash
cd backend

# Unit tests — no database required
pytest tests/unit/ --cov=app --cov-report=term-missing

# Integration tests — requires PostgreSQL
pytest tests/integration/

# Single test
pytest tests/unit/test_auth.py::TestHashPassword::test_hash_password

# Full HTML coverage report
pytest --cov=app --cov-report=html   # open htmlcov/index.html
```

Integration tests connect to `contact_verification_test`. Override the URL if your PostgreSQL is not on `localhost:5432`:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://cvp_user:cvp_password@localhost:5432/contact_verification_test \
  pytest tests/integration/
```

```bash
cd frontend

npm test          # Vitest in watch mode (development)
npm run test:ci   # Single pass with coverage (used by CI)
```

**CI:** GitHub Actions runs three parallel jobs on every push and pull request to `main` — backend unit tests with coverage, backend integration tests against PostgreSQL and Redis service containers, and frontend lint / type-check / tests. Coverage reports are uploaded as workflow artefacts.

---

## Development Phases

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ Complete | Architecture, project structure, configuration |
| 2 | ✅ Complete | Database schema design + Alembic migrations |
| 3 | ✅ Complete | Backend API implementation |
| 4 | ✅ Complete | Verification pipeline + evidence collection |
| 5 | ✅ Complete | Batch CSV processing |
| 6 | ✅ Complete | Frontend implementation |
| 7 | 🔲 Pending | Production Docker configuration + deployment |
| 8 | ✅ Complete | Testing suite + GitHub Actions CI |
| 9 | ✅ Complete | Documentation |

---

## License

MIT
