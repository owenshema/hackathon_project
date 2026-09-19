# Backend

## Run

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Configured for local Postgres:

```
DATABASE_URL=postgresql+asyncpg://postgres:1234@localhost:5432/hackathon_db
USE_PGVECTOR=false
```

API docs: http://localhost:8000/docs
