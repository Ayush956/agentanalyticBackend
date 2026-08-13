# Analytics Dashboard — Solution Architecture & Planning

> **Project:** Full-stack replica of the provided Analytics Dashboard with REST API, PostgreSQL, JWT auth, and AI chatbot helper.

---

## 1. Technology Stack

### Frontend

| Choice | Selection | Justification |
|--------|-----------|---------------|
| Framework | **React 18 + TypeScript** | Mandatory per assignment; TS catches API contract mismatches early. |
| Bundler | **Vite** | Fast HMR, minimal config, excellent DX for SPA dashboards. Lighter than Next.js since we don't need SSR for an authenticated internal dashboard. |
| Styling | **TailwindCSS** | Utility-first speeds up pixel-perfect replication of reference UI; easy responsive layouts and consistent spacing tokens. |
| Charts | **Recharts** | Declarative React components, good defaults for bar/line/pie/area charts used on analytics dashboards; composable with filtered API data. |
| Routing | **React Router v6** | Standard client-side routing for login vs. dashboard views. |
| Data fetching | **TanStack Query (React Query)** | Built-in caching, stale-while-revalidate, deduplication, and loading/error states — reduces boilerplate vs. raw `fetch` + `useEffect`. |
| HTTP client | **Axios** | Interceptors for JWT attachment and 401 redirect; cleaner than wrapping fetch. |
| Forms | **React Hook Form + Zod** | Lightweight login form validation. |

**Why not Next.js?** The dashboard is a authenticated SPA behind login. Vite keeps the frontend simpler, deployable as static assets on CDN/S3 with API on a separate host.

### Backend

| Choice | Selection | Justification |
|--------|-----------|---------------|
| Runtime | **Python 3.11+** | Strong ecosystem for data aggregation, seed scripts, and LLM integration. |
| Framework | **FastAPI** | Async-capable, auto OpenAPI docs, Pydantic validation, preferred by assignment. |
| ORM | **SQLAlchemy 2.0** | Mature relational mapping, composable queries for analytics aggregations. |
| Migrations | **Alembic** | Version-controlled schema changes. |
| Auth | **python-jose (JWT) + passlib (bcrypt)** | Stateless token auth; no server-side session store needed at this scale. |
| Validation | **Pydantic v2** | Request/response schemas shared conceptually with OpenAPI. |

### Database

| Choice | Selection | Justification |
|--------|-----------|---------------|
| DBMS | **PostgreSQL 15+** | Relational model fits Users → Tickets → Escalations with FK integrity; excellent aggregation (`GROUP BY`, window functions, date_trunc) for chart endpoints; indexes on filter columns. |

**Why not MongoDB?** Ticket/escalation data is highly structured with clear relationships and reporting queries that benefit from SQL joins and aggregations.

### AI Chatbot

| Choice | Selection | Justification |
|--------|-----------|---------------|
| LLM | **OpenAI GPT-4o-mini** (configurable) | Cost-effective for structured Q&A; function/tool calling to run safe read-only analytics queries. |
| Pattern | **Tool-calling agent** | LLM maps natural language → predefined analytics service methods (never raw SQL from the model). |

---

## 2. System Architecture

### High-level data flow

```
┌─────────────┐     HTTPS/JWT      ┌──────────────┐     SQL       ┌────────────┐
│  React SPA  │ ◄────────────────► │   FastAPI    │ ◄───────────► │ PostgreSQL │
│  (Vite)     │   REST + /chat     │   Backend    │               │            │
└─────────────┘                    └──────┬───────┘               └────────────┘
                                          │
                                          │ Tool calls (read-only)
                                          ▼
                                   ┌──────────────┐
                                   │  Analytics   │
                                   │  Service     │
                                   └──────────────┘
                                          │
                                          ▼
                                   ┌──────────────┐
                                   │  OpenAI API  │
                                   │  (optional)  │
                                   └──────────────┘
```

1. User logs in → `POST /api/auth/login` → receives JWT.
2. Dashboard mounts → TanStack Query fetches KPI/chart endpoints with shared filter params (date range, division, type, status).
3. Filter changes invalidate/refetch relevant query keys.
4. Chatbot sends user message → `POST /api/chat` → backend uses LLM with tool definitions → tools call analytics service → structured answer returned.

### State management

| Layer | Approach |
|-------|----------|
| Server state | **TanStack Query** — all API data (KPIs, charts, ticket lists). Query keys include filter object for automatic cache segmentation. |
| UI state | **React `useState` / URL search params** — sidebar collapse, active tab, filter panel values synced to URL for shareable dashboard views. |
| Auth state | **Context + localStorage** — store JWT; Axios interceptor attaches `Authorization: Bearer <token>`. |

### Caching strategy

| Location | Strategy |
|----------|----------|
| Client | TanStack Query `staleTime: 30_000` (30s) for dashboard aggregates; `gcTime: 5 * 60_000`. Refetch on window focus disabled during demo to reduce noise. |
| Server | Optional in-memory TTL cache (e.g. `cachetools`) on heavy aggregate endpoints keyed by filter hash — add if profiling shows need. |
| Database | Indexes on `tickets(division, status, type, created_at)`, `escalations(ticket_id, level, status)`. |

### Authentication & token verification

```
Login flow:
  Client POST { email, password }
    → Backend verifies bcrypt hash
    → Returns { access_token, token_type: "bearer", expires_in }

Protected routes:
  Client sends Authorization: Bearer <JWT>
    → FastAPI dependency decode_jwt()
    → Validates signature, expiry, sub (user id)
    → Loads user; 401 if invalid/missing

Frontend guard:
  PrivateRoute checks token existence + optional /auth/me ping
  401 from any API → clear token, redirect /login
```

- **Access token TTL:** 24 hours (configurable via `JWT_EXPIRE_MINUTES`).
- **Algorithm:** HS256 with secret from environment.
- **Password storage:** bcrypt hashes only; never store plaintext.

---

## 3. Directory Structure

```
agentanalyticBackend/
├── PLANNING.md
├── README.md                          # Run instructions (Phase 3)
├── docker-compose.yml                 # Postgres + optional pgAdmin
├── .env.example
│
├── backend/
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/
│   ├── scripts/
│   │   └── seed.py                    # 100+ tickets + escalations
│   └── app/
│       ├── main.py                    # FastAPI app factory
│       ├── config.py                  # Pydantic Settings
│       ├── database.py                # Engine, SessionLocal, get_db
│       ├── dependencies.py            # get_current_user
│       ├── models/
│       │   ├── user.py
│       │   ├── ticket.py
│       │   └── escalation.py
│       ├── schemas/
│       │   ├── auth.py
│       │   ├── ticket.py
│       │   ├── escalation.py
│       │   ├── analytics.py
│       │   └── chat.py
│       ├── routers/
│       │   ├── auth.py
│       │   ├── analytics.py
│       │   ├── tickets.py
│       │   └── chat.py
│       ├── services/
│       │   ├── auth_service.py
│       │   ├── analytics_service.py
│       │   └── chat_service.py
│       └── utils/
│           └── security.py            # hash_password, create_token, decode_token
│
└── frontend/                          # Phase 2 — separate Vite app
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api/                       # Axios + query hooks
        ├── components/
        │   ├── layout/                # Sidebar, Header, UserMenu
        │   ├── charts/                # Recharts wrappers
        │   ├── kpis/                  # KPI cards
        │   ├── filters/               # DateRange, DivisionSelect, etc.
        │   └── chat/                  # AI chatbot panel
        ├── pages/
        │   ├── LoginPage.tsx
        │   └── DashboardPage.tsx
        ├── hooks/
        ├── context/
        │   └── AuthContext.tsx
        └── types/
```

---

## 4. Backend Data Model

### ER overview

```
users (1) ──< tickets (1) ──< escalations
```

### Table: `users`

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK, default gen_random_uuid() |
| email | VARCHAR(255) | UNIQUE, NOT NULL |
| hashed_password | VARCHAR(255) | NOT NULL |
| full_name | VARCHAR(255) | NOT NULL |
| role | VARCHAR(50) | NOT NULL, default `'analyst'` |
| is_active | BOOLEAN | NOT NULL, default TRUE |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

**Indexes:** `UNIQUE (email)`

### Table: `tickets`

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| ticket_number | VARCHAR(50) | UNIQUE, NOT NULL |
| division | VARCHAR(100) | NOT NULL — e.g. IT, HR, Finance, Operations, Legal |
| type | VARCHAR(100) | NOT NULL — e.g. Access Request, Change Request, Incident, Approval |
| status | VARCHAR(50) | NOT NULL — Open, In Review, Approved, Rejected, Closed |
| checker | VARCHAR(255) | NULL — stage assignee name |
| approver | VARCHAR(255) | NULL — stage assignee name |
| checker_completed_at | TIMESTAMPTZ | NULL |
| approver_completed_at | TIMESTAMPTZ | NULL |
| created_at | TIMESTAMPTZ | NOT NULL |
| closed_at | TIMESTAMPTZ | NULL |
| created_by_id | UUID | FK → users.id, ON DELETE SET NULL |
| cycle_time_hours | NUMERIC(10,2) | NULL — computed: closed_at - created_at |

**Indexes:**
- `idx_tickets_division`
- `idx_tickets_type`
- `idx_tickets_status`
- `idx_tickets_created_at`
- Composite: `idx_tickets_filters (division, status, type, created_at DESC)`

### Table: `escalations`

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| ticket_id | UUID | FK → tickets.id, ON DELETE CASCADE, NOT NULL |
| level | INTEGER | NOT NULL — 1, 2, 3 |
| reason | TEXT | NOT NULL |
| status | VARCHAR(50) | NOT NULL — Open, Resolved, Dismissed |
| escalated_at | TIMESTAMPTZ | NOT NULL |
| resolved_at | TIMESTAMPTZ | NULL |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

**Indexes:**
- `idx_escalations_ticket_id`
- `idx_escalations_status`
- `idx_escalations_level`

### Seed data targets (100+ tickets)

| Metric | Target distribution |
|--------|----------------------|
| Total tickets | 150 |
| Divisions | IT 30%, HR 20%, Finance 20%, Operations 20%, Legal 10% |
| Status | Open 15%, In Review 20%, Approved 25%, Rejected 10%, Closed 30% |
| With escalation | ~25% of tickets (1–2 escalations each) |
| Date span | Last 12 months, weighted toward recent months |
| Cycle times | 4h–720h (skewed log-normal for realistic avg ~48–72h) |

---

## 5. API Design Specification

**Base URL:** `/api`  
**Auth:** All routes except `/auth/login` require `Authorization: Bearer <token>`

### Common filter query parameters

Used across analytics endpoints:

| Param | Type | Description |
|-------|------|-------------|
| `start_date` | ISO date | Inclusive filter on `tickets.created_at` |
| `end_date` | ISO date | Inclusive filter |
| `division` | string \| repeated | Filter by division(s) |
| `type` | string \| repeated | Filter by ticket type(s) |
| `status` | string \| repeated | Filter by status(es) |

---

### Auth

#### `POST /api/auth/login`

**Request:**
```json
{
  "email": "analyst@example.com",
  "password": "secret123"
}
```

**Response 200:**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 86400
}
```

**Errors:** `401` invalid credentials, `422` validation error

---

#### `GET /api/auth/me`

**Response 200:**
```json
{
  "id": "uuid",
  "email": "analyst@example.com",
  "full_name": "Jane Analyst",
  "role": "analyst"
}
```

---

### KPI Cards (dashboard top row)

#### `GET /api/analytics/kpis`

**Response 200:**
```json
{
  "total_tickets": 150,
  "open_tickets": 23,
  "avg_cycle_time_hours": 58.4,
  "approval_rate": 0.72,
  "escalation_rate": 0.24,
  "avg_checker_time_hours": 12.1,
  "avg_approver_time_hours": 18.3,
  "closed_this_period": 45
}
```

---

### Charts & widgets

#### `GET /api/analytics/tickets-by-status`

Pie/donut chart.

**Response 200:**
```json
{
  "data": [
    { "status": "Open", "count": 23, "percentage": 15.3 },
    { "status": "Closed", "count": 45, "percentage": 30.0 }
  ]
}
```

---

#### `GET /api/analytics/tickets-by-division`

Bar chart.

**Response 200:**
```json
{
  "data": [
    { "division": "IT", "count": 45, "avg_cycle_time_hours": 52.1 }
  ]
}
```

---

#### `GET /api/analytics/tickets-by-type`

Bar or stacked chart.

**Response 200:**
```json
{
  "data": [
    { "type": "Access Request", "count": 38 }
  ]
}
```

---

#### `GET /api/analytics/cycle-time-trend`

Line chart — monthly avg cycle time.

**Query:** `granularity=month|week` (default `month`)

**Response 200:**
```json
{
  "data": [
    { "period": "2025-08", "avg_cycle_time_hours": 55.2, "ticket_count": 12 }
  ]
}
```

---

#### `GET /api/analytics/ticket-volume-trend`

Area/line chart — tickets created over time.

**Response 200:**
```json
{
  "data": [
    { "period": "2025-08", "count": 12 }
  ]
}
```

---

#### `GET /api/analytics/escalations-by-level`

Bar chart.

**Response 200:**
```json
{
  "data": [
    { "level": 1, "count": 18, "resolved": 12, "open": 6 }
  ]
}
```

---

#### `GET /api/analytics/stage-duration`

Grouped bar — avg checker vs approver duration by division.

**Response 200:**
```json
{
  "data": [
    {
      "division": "IT",
      "avg_checker_hours": 10.5,
      "avg_approver_hours": 16.2
    }
  ]
}
```

---

#### `GET /api/analytics/escalation-reasons`

Top N reasons (table or horizontal bar).

**Query:** `limit=10` (default)

**Response 200:**
```json
{
  "data": [
    { "reason": "SLA breach", "count": 8 }
  ]
}
```

---

### Ticket list (drill-down table)

#### `GET /api/tickets`

**Query:** filters + pagination

| Param | Default |
|-------|---------|
| `page` | 1 |
| `page_size` | 20 (max 100) |
| `sort_by` | created_at |
| `sort_order` | desc |

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "ticket_number": "TKT-2025-0042",
      "division": "IT",
      "type": "Access Request",
      "status": "Closed",
      "checker": "Alice",
      "approver": "Bob",
      "created_at": "2025-07-01T10:00:00Z",
      "closed_at": "2025-07-03T14:00:00Z",
      "cycle_time_hours": 52.0,
      "escalation_count": 1
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 20,
  "total_pages": 8
}
```

---

#### `GET /api/tickets/{id}`

Single ticket with nested escalations.

**Response 200:**
```json
{
  "id": "uuid",
  "ticket_number": "TKT-2025-0042",
  "division": "IT",
  "escalations": [
    {
      "id": "uuid",
      "level": 1,
      "reason": "SLA breach",
      "status": "Resolved",
      "escalated_at": "...",
      "resolved_at": "..."
    }
  ]
}
```

**Errors:** `404` not found

---

### Filter metadata

#### `GET /api/analytics/filters`

Returns distinct values for populating filter dropdowns.

**Response 200:**
```json
{
  "divisions": ["IT", "HR", "Finance", "Operations", "Legal"],
  "types": ["Access Request", "Change Request", "Incident", "Approval"],
  "statuses": ["Open", "In Review", "Approved", "Rejected", "Closed"]
}
```

---

### AI Chatbot

#### `POST /api/chat`

**Request:**
```json
{
  "message": "What is our average approval cycle time in IT?",
  "conversation_id": "optional-uuid"
}
```

**Response 200:**
```json
{
  "reply": "The average approval cycle time for IT tickets is 52.1 hours.",
  "data": {
    "avg_cycle_time_hours": 52.1,
    "division": "IT",
    "ticket_count": 45
  },
  "conversation_id": "uuid"
}
```

**Tool functions exposed to LLM (read-only):**
- `get_kpis(filters)`
- `get_tickets_by_division(filters)`
- `get_avg_cycle_time(division?, type?)`
- `count_by_status(status, division?)`
- `get_escalation_summary(filters)`

**Errors:** `503` if OpenAI key missing (graceful fallback message), `401` unauthorized

---

### HTTP status code summary

| Code | Usage |
|------|-------|
| 200 | Success |
| 201 | Created (if needed) |
| 400 | Bad request / invalid filter combo |
| 401 | Missing or invalid JWT |
| 404 | Resource not found |
| 422 | Pydantic validation error |
| 500 | Unexpected server error |
| 503 | LLM service unavailable |

---

## 6. Frontend Dashboard Mapping

> **Note:** Replicate layouts, colors, spacing, and interactions from the **provided deployed reference link**. The widget-to-endpoint mapping below assumes a standard analytics dashboard; adjust component names after visual audit of the reference.

| UI Widget | API Endpoint | Chart type |
|-----------|--------------|------------|
| Total Tickets KPI | `GET /analytics/kpis` | Stat card |
| Open Tickets KPI | `GET /analytics/kpis` | Stat card |
| Avg Cycle Time KPI | `GET /analytics/kpis` | Stat card |
| Approval Rate KPI | `GET /analytics/kpis` | Stat card |
| Escalation Rate KPI | `GET /analytics/kpis` | Stat card |
| Status breakdown | `GET /analytics/tickets-by-status` | Donut |
| Division performance | `GET /analytics/tickets-by-division` | Bar |
| Ticket types | `GET /analytics/tickets-by-type` | Bar |
| Cycle time trend | `GET /analytics/cycle-time-trend` | Line |
| Volume trend | `GET /analytics/ticket-volume-trend` | Area |
| Escalations by level | `GET /analytics/escalations-by-level` | Bar |
| Stage duration | `GET /analytics/stage-duration` | Grouped bar |
| Ticket table | `GET /tickets` | Data table |
| Date/Division/Type filters | Query params on all analytics calls | — |
| User menu | `GET /auth/me` + logout (clear token) | — |
| AI Chat panel | `POST /chat` | Chat UI |

---

## 7. Verification & Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+ (for frontend, Phase 2)
- Docker (recommended for Postgres)

### Backend quick start

```bash
# 1. Start Postgres
docker compose up -d db

# 2. Backend setup
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # edit DATABASE_URL, JWT_SECRET, OPENAI_API_KEY

# 3. Run migrations & seed
alembic upgrade head
python scripts/seed.py

# 4. Start API
uvicorn app.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- Default login (after seed): `analyst@example.com` / `secret123`

### Frontend quick start (Phase 2)

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_URL=http://localhost:8000/api
npm run dev
```

---

## 8. Deployment Plan

### Recommended: Render (simple) or Railway

```
┌─────────────────────────────────────────────────────────┐
│  Render / Railway                                       │
├─────────────────────────────────────────────────────────┤
│  Web Service (FastAPI)                                  │
│    - Build: pip install -r requirements.txt             │
│    - Start: uvicorn app.main:app --host 0.0.0.0 --port $PORT │
│    - Env: DATABASE_URL, JWT_SECRET, OPENAI_API_KEY, CORS_ORIGINS │
├─────────────────────────────────────────────────────────┤
│  PostgreSQL (managed add-on)                            │
│    - Run alembic upgrade head on deploy                 │
│    - One-off job: python scripts/seed.py (staging only) │
├─────────────────────────────────────────────────────────┤
│  Static Site (Vite build)                               │
│    - Build: npm run build                               │
│    - Env: VITE_API_URL=https://api.yourapp.com/api      │
│    - SPA rewrite: /* → index.html                       │
└─────────────────────────────────────────────────────────┘
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET` | Strong random secret |
| `JWT_EXPIRE_MINUTES` | Token TTL (default 1440) |
| `CORS_ORIGINS` | Comma-separated frontend URLs |
| `OPENAI_API_KEY` | For chatbot (optional in dev) |

### Production checklist

- [ ] HTTPS only (platform-provided TLS)
- [ ] Strong `JWT_SECRET` via secrets manager
- [ ] CORS restricted to frontend origin
- [ ] DB connection pooling (`pool_pre_ping=True`)
- [ ] Health check endpoint `GET /health`
- [ ] Do not run seed script in production

---

## 9. Implementation Phases (Execution Order)

| Phase | Task | Status |
|-------|------|--------|
| 1 | PLANNING.md (this document) | ✅ |
| 2a | FastAPI scaffold, models, auth | 🔄 In progress |
| 2b | Analytics service + endpoints | Pending |
| 2c | Seed script (150 tickets) | Pending |
| 2d | Frontend Vite app + login | Pending |
| 2e | Dashboard UI replication | Pending |
| 2f | AI chatbot integration | Pending |
| 3 | README + deployment config | Pending |

---

## 10. Open Items

1. **Reference dashboard URL** — Audit deployed app for exact colors, sidebar items, chart order, and filter UX; update Section 6 mapping if widgets differ.
2. **Branding** — Extract primary/secondary hex values from reference during frontend phase.
3. **LLM provider** — OpenAI default; swap via env if reviewer prefers Anthropic/Gemini.
