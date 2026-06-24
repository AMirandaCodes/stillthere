# Contact Verification Platform

A full-stack web application that verifies whether a business contact is likely still employed at a company by collecting and analysing publicly available information from the web.

> **Evidence-first**: every result is backed by a cited source. If sufficient evidence cannot be found, the result is "Unclear" — the system never guesses or infers.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Development](#local-development)
- [Environment Variables](#environment-variables)
- [API Documentation](#api-documentation)
- [Running Tests](#running-tests)
- [Development Phases](#development-phases)

---

## Overview

The platform supports two workflows:

| Workflow | Description |
|---|---|
| **Single Verification** | Enter a name, company, and optional email → receive a detailed evidence-backed report |
| **Batch CSV Upload** | Upload a CSV of contacts → background processing with a live progress dashboard |

Each verification report includes:
- **Person Found Online** (Yes / No / Unclear)
- **Appears Associated With Company** (Yes / No / Unclear)
- **Found On Company Website** (Yes / No / Unclear)
- **Company Active / Still Trading** (Yes / No / Unclear)
- **Email Match Found** (Yes / No / Unclear)
- **Confidence Score** (0–100)
- **Confidence Level** (High / Medium / Low)
- **Evidence Sources** — URL, title, collection date, reasoning
- **Useful Links** — company website, profile pages

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                       Browser (React + TS)                          │
│   Home │ Batch Upload │ Results │ History │ Jobs Dashboard          │
└─────────────────────────┬──────────────────────────────────────────┘
                          │ REST / JSON
┌─────────────────────────▼──────────────────────────────────────────┐
│                    FastAPI (Python 3.11)                             │
│                                                                      │
│  ┌─────────────┐   ┌───────────────┐   ┌─────────────────────────┐ │
│  │  API Routes  │──▶│ Service Layer │──▶│  Repository Layer       │ │
│  │  (v1/)      │   │  (business    │   │  (SQLAlchemy async ORM) │ │
│  └─────────────┘   │   logic)      │   └────────────┬────────────┘ │
│                    └───────┬───────┘                │               │
│                            │ enqueue                ▼               │
│                    ┌───────▼───────┐        ┌──────────────┐        │
│                    │ Celery Tasks  │        │  PostgreSQL   │        │
│                    │  (workers)    │        │  (main DB)    │        │
│                    └───────┬───────┘        └──────────────┘        │
└────────────────────────────┼───────────────────────────────────────┘
                             │ broker / results
┌────────────────────────────▼───────────────────────────────────────┐
│                          Redis                                       │
└────────────────────────────────────────────────────────────────────┘

Verification Pipeline (runs inside Celery worker):
  SearchService     ──▶  Serper API (Google Search)
  EvidenceService   ──▶  httpx page fetches + BeautifulSoup parsing
  AI Analyser       ──▶  Anthropic Claude API (evidence extraction)
  ConfidenceService ──▶  deterministic scoring (no inference)
```

### Key Design Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| Async verification | Celery background tasks | Avoids HTTP timeouts; allows batch processing |
| Evidence analysis | Claude API | Structured extraction from unstructured web text; can be told to return `unclear` when evidence is absent |
| Web search | Serper.dev | Programmatic Google results; 2,500 free searches/month; no scraping Google directly |
| Page fetching | httpx + BS4 | Async, respects server-set delays, easy HTML → text |
| ORM | SQLAlchemy 2.0 async | Native async support, excellent type hints, Alembic migration support |
| Architecture | Layered monolith | Simpler than microservices for this scale; layers are explicit and easily separable later |

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
| Logging | structlog (structured JSON in prod) |
| Containers | Docker + Docker Compose |
| Testing | pytest, pytest-asyncio, Vitest |

---

## Project Structure

```
contact-verification-platform/
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/      # HTTP route handlers (thin — no business logic)
│   │   ├── core/               # Config, logging, security utilities
│   │   ├── db/                 # SQLAlchemy engine + session factory
│   │   ├── models/             # ORM table definitions
│   │   ├── repositories/       # Database access layer (one class per entity)
│   │   ├── schemas/            # Pydantic request/response models
│   │   ├── services/           # Business logic orchestration
│   │   ├── tasks/              # Celery task definitions
│   │   └── main.py             # FastAPI application factory
│   ├── alembic/                # Database migrations
│   ├── tests/
│   │   ├── unit/               # Service + schema tests (no DB)
│   │   └── integration/        # API endpoint + DB interaction tests
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── components/         # Reusable UI building blocks
│       ├── pages/              # Route-level page components
│       ├── services/           # API call wrappers
│       ├── types/              # TypeScript type definitions
│       ├── hooks/              # Custom React hooks (polling, mutations)
│       └── utils/              # Formatters, validators
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/your-username/contact-verification-platform.git
cd contact-verification-platform

# 2. Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and SERPER_API_KEY at minimum

# 3. Start all services
docker compose up --build

# 4. Run database migrations
docker compose exec backend alembic upgrade head

# Services available at:
#   Frontend:        http://localhost:5173
#   API:             http://localhost:8000
#   Swagger UI:      http://localhost:8000/api/docs
#   Flower (tasks):  http://localhost:5555
```

---

## Local Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start PostgreSQL and Redis (via Docker)
docker compose up db redis -d

# Apply migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key from console.anthropic.com |
| `SERPER_API_KEY` | Yes | Google Search API key from serper.dev |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `SECRET_KEY` | Yes | Random string for signing (min 32 chars) |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |
| `DEBUG` | No | Set `true` for verbose logging |

See [`.env.example`](.env.example) for all available variables.

---

## API Documentation

Interactive documentation is auto-generated by FastAPI:

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc
- **OpenAPI JSON**: http://localhost:8000/api/openapi.json

### Core Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/verifications` | Submit a new verification |
| `GET` | `/api/v1/verifications/{id}` | Get verification result |
| `GET` | `/api/v1/verifications` | List all verifications (paginated) |
| `POST` | `/api/v1/batch/upload` | Upload CSV for batch processing |
| `GET` | `/api/v1/batch/{id}` | Get batch job status |
| `GET` | `/api/v1/batch/{id}/results` | Get job results (paginated) |
| `GET` | `/api/v1/batch/{id}/export` | Download results CSV |
| `GET` | `/api/v1/health` | Health check |

---

## Running Tests

```bash
cd backend

# All tests
pytest

# Unit tests only (no DB required)
pytest tests/unit/

# Integration tests (requires running PostgreSQL)
pytest tests/integration/

# With coverage report
pytest --cov=app --cov-report=html
```

---

## Development Phases

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ **Complete** | Architecture, project structure, configuration |
| 2 | Pending | Database schema design + Alembic migrations |
| 3 | Pending | Backend API implementation |
| 4 | Pending | Verification pipeline + evidence collection |
| 5 | Pending | Batch CSV processing |
| 6 | Pending | Frontend implementation |
| 7 | Pending | Docker production configuration |
| 8 | Pending | Testing suite |
| 9 | Pending | Documentation + README diagrams |

---

## License

MIT
