# UniPods Memory AI

> Ask what happened. Find what was decided. Know what you missed.

Community memory engine for the UniPods hackathon: WhatsApp + Teams + meeting transcripts → RAG answers with an **evidence trail you can replay**.

## Stack

| Layer | Choice |
|--------|--------|
| Backend | Python FastAPI |
| Frontend | Next.js (admin / demo chat) |
| Database | PostgreSQL + pgvector |
| LLM | Groq (primary) · Gemini (fallback) |
| Platforms | WhatsApp Cloud API (MVP) · Teams Bot (stretch) |

## Project layout

```
hackathon_project/
├── backend/                 # FastAPI API + RAG + adapters
│   └── app/
│       ├── adapters/        # WhatsApp & Teams → NormalizedMessage
│       ├── api/routes/      # chat, catchup, ingest, evidence, webhooks
│       ├── db/              # SQLAlchemy models (messages, chunks, decisions)
│       ├── schemas/         # Pydantic contracts
│       └── services/        # ingestion, RAG, extraction, evidence replay
├── frontend/                # Next.js chat + upload + evidence UI
├── docker-compose.yml       # Optional local pgvector (if not using your own Postgres)
├── .env.example
└── README.md
```

## How it works

```
WhatsApp / Teams / Web / Upload
        ↓
  Platform adapters (one schema)
        ↓
  Raw message store (Postgres)  ← citations live here
        ↓
  Chunk + embed (pgvector)
        ↓
  RAG → Answer + Evidence + Timestamp
        ↓
  Response router → same platform
```

**Evidence Replay:** every answer can include WhatsApp messages, voice notes, meeting timestamps (e.g. `42:18`), and a replay link.

## Quick start

### 1. Database

Your local Postgres:

- **Database:** `hackathon_db`
- **User:** `postgres`
- **Password:** `1234`

`hackathon_db` already exists on this machine. **pgvector is not installed** on Windows Postgres 18, so the app stores embeddings as JSON and ranks with Python cosine similarity (`USE_PGVECTOR=false`). That is fine for the hackathon demo.

Optional later: install [pgvector](https://github.com/pgvector/pgvector) or run `docker compose up -d`, then set `USE_PGVECTOR=true`.

### 2. Backend

```bash
cd backend
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
copy ..\.env.example .env
```

Edit `backend/.env` (or repo root `.env`) so it matches your DB:

```env
DATABASE_URL=postgresql+asyncpg://postgres:1234@localhost:5432/hackathon_db
GROQ_API_KEY=your_key_here
```

Run the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API docs: http://localhost:8000/docs  
- Health: http://localhost:8000/api/v1/health  

Seed the cross-source demo (WhatsApp + meeting + confirmation):

```bash
curl -X POST http://localhost:8000/api/v1/ingest/seed-demo
```

### 3. Frontend

```bash
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000 — ask *"What did they decide about the database?"* after seeding.

## WhatsApp (MVP)

1. Meta Developer App → WhatsApp Cloud API  
2. Set webhook URL to `https://<your-tunnel>/api/v1/webhooks/whatsapp`  
3. Verify token = `WHATSAPP_VERIFY_TOKEN` in `.env`  
4. Fill `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`

Commands in the group: `/ask …` · `/catchup` · `/decisions` · `/tasks`

## Teams (stretch)

- Live bot: `POST /api/v1/webhooks/teams` (Bot Framework)  
- MVP meetings: upload a transcript via the web UI or `POST /api/v1/ingest/transcript`

## Core API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/chat` | Ask / commands (web) |
| POST | `/api/v1/catchup` | Personalized Catch Me Up |
| POST | `/api/v1/ingest/transcript` | Meeting transcript upload |
| POST | `/api/v1/ingest/seed-demo` | Demo evidence pack |
| GET/POST | `/api/v1/webhooks/whatsapp` | WhatsApp Cloud API |
| POST | `/api/v1/webhooks/teams` | Teams bot activities |
| GET | `/api/v1/evidence/{id}` | Citation payload |
| GET | `/api/v1/evidence/replay` | Replay seek / voice |

## MVP vs stretch

**MVP (hackathon week):** WhatsApp bot · web chat · manual transcript upload · RAG + citations · Catch Me Up · Evidence Replay  

**Stretch:** live Teams bot · Graph auto-ingest of recordings · voice STT · personal briefing · change detection
