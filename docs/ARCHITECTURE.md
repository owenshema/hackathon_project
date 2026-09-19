# Architecture

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│  WhatsApp Cloud API   │  Teams Bot Framework  │  Next.js UI │
└───────────┬───────────┴───────────┬───────────┴──────┬──────┘
            │                       │                  │
            ▼                       ▼                  ▼
     WhatsAppAdapter          TeamsAdapter        /api/v1/chat
            └───────────┬───────────┘                  │
                        ▼                              │
              NormalizedMessage                        │
                        ▼                              │
              Ingestion → Message (Postgres)           │
                        ▼                              │
              Chunk + embedding (pgvector)             │
                        ▼                              │
         ┌──────────────┴──────────────┐               │
         ▼                             ▼               │
   RAG answer_question          extract / catchup      │
         │                             │               │
         └──────────────┬──────────────┘               │
                        ▼                              │
               MemoryAnswer + EvidenceItem ◄───────────┘
                        ▼
               Response router → same platform
```

## Key modules

| Path | Role |
|---|---|
| `adapters/` | Platform → `NormalizedMessage` + `send_reply` |
| `services/ingestion.py` | Persist raw citation sources |
| `services/embeddings.py` | sentence-transformers → 384-d vectors |
| `services/rag.py` | Retrieve + LLM answer with evidence indices |
| `services/extraction.py` | One pass: important / decisions / discussions / tasks |
| `services/evidence.py` | Build replayable citation objects |
| `services/response_router.py` | Commands → answer → platform text |
| `services/meetings.py` | Timestamped transcript parse + index |
| `api/routes/webhooks.py` | WhatsApp verify + inbound; Teams activities |

## Data model (citation-ready)

- **Message** — every chat line, voice transcript, meeting segment (source of truth for citations)
- **Chunk** — embedded slice linked to message/meeting + speaker + offset
- **Decision** / **ActionItem** — structured extracts with evidence FKs
- **Meeting** — uploaded transcript/audio metadata
- **UserActivity** — last-seen for Catch Me Up

## Evidence Replay

1. RAG returns chunk IDs → mapped to `EvidenceItem`
2. Meeting segments carry `meeting_offset_sec` → display `42:18`
3. `GET /api/v1/evidence/replay?meeting_id=&t=` returns seek hint
4. Voice: `?voice=<message_id>` returns media pointer
5. Frontend **Replay evidence** opens the trail item

## MVP vs stretch

**MVP**

- Manual transcript upload
- WhatsApp bot in group
- Web admin chat
- Seeded cross-source demo

**Stretch**

- Live Teams bot replies
- Graph change notifications for new recordings/transcripts
- Full media streaming with Range requests
- Morning personal briefings / change detection polish
