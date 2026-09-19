"""RAG retrieval + evidence-first answer generation."""

from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Chunk, Decision, Message
from app.schemas.memory import EvidenceItem, MemoryAnswer
from app.services.embeddings import embed_texts
from app.services.evidence import evidence_from_chunk, evidence_from_message
from app.services.llm import generate_json

SYSTEM_PROMPT = """You are UniPods Memory AI — a helpful community memory assistant in a WhatsApp group.
Your job is to answer questions, clarify decisions, and help team members stay caught up.

Guidelines:
1. Answer directly, naturally, and accurately based on the provided Evidence. If evidence is insufficient to answer the question, state that clearly (set confidence to "insufficient").
2. Keep the answer direct, friendly, and concise for mobile WhatsApp reading.
3. Do NOT add robotic introductory boilerplate like "According to the group admin:" or "According to [Author]:". Simply state the answer directly.
4. If asked to remind or address someone, speak directly and naturally.
5. Never invent facts not supported by the evidence.
6. Return JSON with keys:
   - "answer": Clean, concise answer formatted for WhatsApp
   - "confidence": "high" | "medium" | "low" | "insufficient"
   - "decision": (optional short string if a concrete decision was made, else null)
   - "reason": (optional string, else null)
   - "evidence_indices": list of 0-based integer indices of the evidence items directly supporting your answer
"""


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


async def retrieve(
    db: AsyncSession,
    query: str,
    limit: int = 8,
    *,
    conversation_id: str | None = None,
    exclude_message_ids: set | None = None,
) -> list[Chunk]:
    vectors = embed_texts([query])
    query_vec = vectors[0] if vectors else None
    exclude = exclude_message_ids or set()

    # Load recent chunks (optionally scoped later via message join)
    result = await db.execute(select(Chunk).order_by(Chunk.created_at.desc()).limit(1000))
    chunks = list(result.scalars().all())

    if exclude:
        chunks = [c for c in chunks if c.message_id not in exclude]

    if conversation_id:
        # Prioritize chunks from the same group/chat while keeping documentation/knowledge accessible
        same_conv = []
        other_chunks = []
        for c in chunks:
            meta = c.meta or {}
            c_conv = meta.get("conversation_id")
            if c_conv == conversation_id or (not c.message_id):  # Meeting/doc chunks have no message_id
                same_conv.append(c)
            else:
                other_chunks.append(c)
        chunks = same_conv + other_chunks

    if settings.store_embeddings_as_json or not query_vec or all(v == 0.0 for v in query_vec):
        if not query_vec or all(v == 0.0 for v in query_vec):
            import re
            stop = {
                "the", "and", "for", "are", "but", "not", "you", "all", "can",
                "had", "her", "was", "one", "our", "out", "day", "get", "has",
                "him", "his", "how", "man", "new", "now", "old", "see", "two",
                "way", "who", "boy", "did", "its", "let", "put", "say", "she",
                "too", "use", "what", "when", "they", "them", "this", "that",
                "with", "have", "from", "about", "into", "than", "then", "there",
                "someone", "anyone", "please", "could", "would", "tell", "clarify",
                "explain", "about", "does", "been", "where", "which"
            }
            # Clean words from query
            clean_q = re.sub(r"[^\w\s]", " ", query.lower())
            tokens = [t for t in clean_q.split() if len(t) > 2 and t not in stop]

            if not tokens:
                return chunks[:limit]

            def score(c: Chunk) -> float:
                text = (c.content or "").lower()
                s = 0.0
                # Exact phrase match bonus
                has_match = False
                if clean_q.strip() and clean_q.strip() in text:
                    s += 10.0
                    has_match = True

                # Token matching with prefix/stemming support
                for t in tokens:
                    if t in text:
                        s += 3.0
                        has_match = True
                    elif len(t) >= 4 and any(w.startswith(t[:4]) for w in text.split()):
                        s += 1.5
                        has_match = True

                # If no tokens or phrases matched the text, this chunk is irrelevant
                if not has_match:
                    return 0.0

                # Admin author bonus (only if content is relevant)
                author = (c.author_name or "").lower()
                if "admin" in author or "organizer" in author:
                    s += 2.0

                # Announcement indicator bonus (only if content is relevant)
                if any(k in text for k in ("announcement", "deadline", "note", "remember", "important", "update", "decision")):
                    s += 1.5

                return s

            scored = [(score(c), c) for c in chunks]
            scored = [(s, c) for s, c in scored if s > 0]
            scored.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in scored[:limit]]

        ranked = []
        for c in chunks:
            emb = c.embedding
            if not emb:
                continue
            ranked.append((_cosine(query_vec, list(emb)), c))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked[:limit]]

    from sqlalchemy import text

    sql = text(
        """
        SELECT id FROM chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> :embedding
        LIMIT :limit
        """
    )
    rows = await db.execute(sql, {"embedding": str(query_vec), "limit": limit * 3})
    ids = [r[0] for r in rows.fetchall()]
    if not ids:
        return []
    result = await db.execute(select(Chunk).where(Chunk.id.in_(ids)))
    found = list(result.scalars().all())
    if exclude:
        found = [c for c in found if c.message_id not in exclude]
    order = {cid: i for i, cid in enumerate(ids)}
    found.sort(key=lambda c: order.get(c.id, 999))
    return found[:limit]


async def answer_question(
    db: AsyncSession,
    question: str,
    *,
    mode: str | None = None,
    fast: bool = False,
    conversation_id: str | None = None,
    exclude_message_ids: set | None = None,
    require_evidence: bool = False,
) -> MemoryAnswer:
    chunks = await retrieve(
        db,
        question,
        limit=5 if fast else 8,
        conversation_id=conversation_id,
        exclude_message_ids=exclude_message_ids,
    )
    if not chunks:
        return MemoryAnswer(
            answer=(
                "I couldn't find that in the shared group chats yet."
                if require_evidence
                else "I couldn't find enough evidence to answer that."
            ),
            confidence="insufficient",
            evidence=[],
        )

    evidence_blocks = []
    for i, chunk in enumerate(chunks):
        offset = ""
        if chunk.meeting_offset_sec is not None:
            m, s = divmod(int(chunk.meeting_offset_sec), 60)
            offset = f" @{m}:{s:02d}"
        evidence_blocks.append(
            f"[{i}] ({chunk.platform or 'unknown'} | {chunk.author_name or 'unknown'}"
            f"{offset}) {chunk.content}"
        )

    mode_hint = ""
    if mode == "30sec" or fast:
        mode_hint = "Keep the answer to one or two short sentences."
    elif mode == "decisions":
        mode_hint = "Focus only on decisions."
    elif mode == "tasks":
        mode_hint = "Focus only on action items / tasks."

    prompt = (
        f"Question: {question}\n{mode_hint}\n\n"
        f"Evidence:\n" + "\n".join(evidence_blocks) + "\n\n"
        "Respond with JSON only."
    )
    data = await generate_json(prompt, SYSTEM_PROMPT, fast=fast)

    confidence_raw = data.get("confidence", "medium")
    if isinstance(confidence_raw, (int, float)):
        confidence = "high" if confidence_raw >= 0.7 else ("medium" if confidence_raw >= 0.4 else "insufficient")
    else:
        confidence = str(confidence_raw).lower()

    indices = data.get("evidence_indices") or []
    if not isinstance(indices, list):
        indices = []

    # If the LLM has confidence, ensure at least one supporting chunk is cited
    if confidence != "insufficient":
        if not indices and chunks:
            indices = [0]
    else:
        indices = []

    evidence: list[EvidenceItem] = []
    seen: set[int] = set()
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < len(chunks) and idx not in seen:
            seen.add(idx)
            evidence.append(evidence_from_chunk(chunks[idx]))

    answer_text = data.get("answer") or "I couldn't find enough evidence to answer that."
    if confidence == "insufficient" or not evidence:
        confidence = "insufficient"
        evidence = []
        answer_text = (
            "I couldn't find that in the shared group chats yet."
            if require_evidence
            else "I couldn't find enough evidence to answer that."
        )

    return MemoryAnswer(
        answer=answer_text,
        confidence=confidence,
        decision=data.get("decision"),
        reason=data.get("reason"),
        evidence=evidence,
        command="ask",
    )


async def list_decisions(db: AsyncSession, query: str = "") -> MemoryAnswer:
    result = await db.execute(
        select(Decision).order_by(Decision.created_at.desc()).limit(20)
    )
    decisions = list(result.scalars().all())
    if not decisions:
        return await answer_question(db, query or "What decisions have we made?")

    lines = [f"- {d.decision}" + (f" ({d.reason})" if d.reason else "") for d in decisions]
    evidence: list[EvidenceItem] = []
    for d in decisions[:5]:
        if d.evidence_message_id:
            msg = await db.get(Message, d.evidence_message_id)
            if msg:
                evidence.append(evidence_from_message(msg))

    return MemoryAnswer(
        answer="Decisions on record:\n" + "\n".join(lines),
        confidence="high",
        evidence=evidence,
        command="decisions",
    )
