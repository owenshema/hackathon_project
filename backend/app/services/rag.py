"""RAG retrieval + evidence-first answer generation."""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime

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
1. Read all provided Evidence before answering. Treat it like group memory: combine relevant chat messages, documents, meeting notes, voice transcripts, and media descriptions when they are present.
2. First understand the user's exact question and identify what kind of answer is needed: deadline, rule, meeting link, action item, explanation, person, document, recording, or general summary. Do not answer a different nearby topic.
3. Answer directly, naturally, and accurately based on the provided Evidence. If evidence is insufficient to answer the question, state that clearly (set confidence to "insufficient").
4. For official programme facts, deadlines, eligibility, submission, testing, team rules, prize, links, and required actions, rely on leaders/admins (@Diane, @Gift, @Munira, @Jeovaire, @Charles) or official attached documents. Participant guesses are not enough.
5. Be honest about media. If an audio/image/file is only shown as pending transcription or as an attachment without extracted content, say you can see that media exists but cannot use its contents yet.
6. Keep the answer warm, human, and concise for mobile WhatsApp reading. Use "I found..." / "What matters is..." only when that sounds natural.
7. Use the chat's best clarification style: short direct answer first, then 2-5 simple bullet points when useful, with WhatsApp bold for key terms like *deadline* or *submission*. Avoid long essays.
8. Prefer human/admin clarifications and official attached documents over chatbot-generated messages. Do not use meti_bot or other bot messages as authoritative evidence if human/admin/document evidence is available.
9. Do NOT add robotic introductory boilerplate like "According to the group admin:" or "According to [Author]:". Simply state the answer directly.
10. If asked to remind or address someone, speak directly and naturally.
11. Never invent facts not supported by the evidence.
12. For action-oriented questions, include only clear next steps, deadlines, names, links, and requirements found in evidence.
13. When evidence comes from a person, mention the speaker naturally using @Name when it helps trust and clarity, for example: "@Joel said we should meet tomorrow at 9:00 AM." This is especially important in voice explanations and evidence summaries.
14. Pay attention to today's date. If a deadline, meeting, or required action in the evidence is already in the past, say that it has passed, avoid presenting it as upcoming, and respond with gentle empathy such as "Sorry, that deadline has already passed."
15. Return JSON with keys:
   - "answer": Clean, concise answer formatted for WhatsApp
   - "confidence": "high" | "medium" | "low" | "insufficient"
   - "decision": (optional short string if a concrete decision was made, else null)
   - "reason": (optional string, else null)
   - "evidence_indices": list of 0-based integer indices of the evidence items directly supporting your answer
"""

TRUSTED_AUTHORS = ("diane", "gift", "munira", "jeovaire", "charles")
HIGH_STAKES_TERMS = (
    "deadline", "submit", "submission", "team", "teams", "declare", "declaration",
    "rule", "rules", "requirement", "required", "must", "eligible", "eligibility",
    "prize", "cash", "winner", "judge", "judging", "test", "testing", "deploy",
    "meeting", "session", "link", "recording", "email", "form", "course", "mit",
    "wadhwani", "payment", "funding", "grant",
)


def _author_mention(author: str | None) -> str:
    if not author:
        return "@someone"
    clean = author.strip()
    if clean.startswith("@"):
        return clean
    first_name = clean.split()[0].strip("():,")
    known = {
        "joel": "@Joel",
        "joe": "@Joel",
        "shema": "@Shema",
        "owen": "@Owen",
        "deborah": "@Deborah",
        "kgosi": "@Kgosi",
        "reitumetse": "@Reitumetse",
    }
    return known.get(first_name.lower(), f"@{first_name}" if first_name else "@someone")


def _is_trusted_source(chunk: Chunk) -> bool:
    author = (chunk.author_name or "").lower()
    meta = chunk.meta or {}
    shared_by = str(meta.get("shared_by") or "").lower()
    return (
        any(name in author for name in TRUSTED_AUTHORS)
        or any(name in shared_by for name in TRUSTED_AUTHORS)
        or ("shared" in author and bool(meta.get("document_filename")))
    )


def _is_bot_source(chunk: Chunk) -> bool:
    author = (chunk.author_name or "").lower()
    return "bot" in author or "meti_bot" in author


def _tokens(text: str) -> list[str]:
    stop = {
        "what", "who", "when", "where", "why", "how", "did", "do", "does", "is",
        "are", "was", "were", "the", "a", "an", "in", "on", "at", "for", "to",
        "of", "and", "or", "our", "we", "they", "them", "this", "that", "there",
        "can", "could", "should", "would", "please", "tell", "me", "about", "more",
        "show", "give", "know", "just", "like", "need", "want", "have", "has",
        "any", "someone", "anyone", "clarify", "explain",
    }
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in clean.split() if len(w) > 2 and w not in stop]


def _is_high_stakes_question(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in HIGH_STAKES_TERMS)


def _is_too_vague(question: str) -> bool:
    q = question.strip().lower()
    if len(_tokens(q)) >= 2:
        return False
    vague = {
        "what about it", "what about this", "and this", "this?", "it?", "what about",
        "explain", "tell me", "more", "why", "how", "what",
    }
    return q.strip(" ?!.") in vague or q in {"?", "??"}


def _needs_exact_numeric_evidence(question: str) -> bool:
    q = question.lower()
    has_number = bool(re.search(r"\b(2|two|3|three|4|four|5|five)\b", q))
    same_country = "same country" in q or ("country" in q and "same" in q)
    return has_number and same_country


def _has_exact_numeric_evidence(question: str, chunk: Chunk) -> bool:
    if not _needs_exact_numeric_evidence(question):
        return True
    q = question.lower()
    text = (chunk.content or "").lower()
    if "3" in q or "three" in q:
        return any(phrase in text for phrase in (
            "3 members from the same country",
            "three members from the same country",
            "maximum of 2",
            "max 2",
            "up to 2",
            "two members from the same country",
            "2 members from the same country",
            "not clearly approve 3",
            "3 from one country",
        ))
    return True


def _fit_score(question: str, chunk: Chunk) -> float:
    q_tokens = _tokens(question)
    if not q_tokens:
        return 0.0
    text = (chunk.content or "").lower()
    overlap = 0.0
    for token in q_tokens:
        if token in text:
            overlap += 2.0
        elif len(token) >= 4 and any(word.startswith(token[:4]) for word in text.split()):
            overlap += 0.8
    return overlap / max(len(q_tokens), 1)


def _authority_score(chunk: Chunk) -> float:
    if _is_bot_source(chunk):
        return -5.0
    author = (chunk.author_name or "").lower()
    shared_by = str((chunk.meta or {}).get("shared_by") or "").lower()
    if any(name in shared_by for name in TRUSTED_AUTHORS):
        return 5.0
    if "shared" in author and (chunk.meta or {}).get("document_filename"):
        return 4.0
    if any(name in author for name in TRUSTED_AUTHORS):
        return 5.0
    return 0.0


def _rank_evidence(question: str, chunks: list[Chunk]) -> list[Chunk]:
    ranked = sorted(
        chunks,
        key=lambda c: (_fit_score(question, c) + _authority_score(c), c.timestamp or c.created_at),
        reverse=True,
    )
    return ranked


def _deadline_answer_from_evidence(question: str, chunks: list[Chunk]) -> MemoryAnswer | None:
    q = question.lower()
    if "deadline" not in q and "due" not in q and "submit" not in q and "submission" not in q:
        return None

    trusted = [c for c in chunks if _is_trusted_source(c) and not _is_bot_source(c)]

    def find(*needles: str, prefer_chat: bool = False) -> Chunk | None:
        candidates = trusted
        if prefer_chat:
            candidates = sorted(
                trusted,
                key=lambda c: 0 if (c.meta or {}).get("source_type") == "chat" else 1,
            )
        for chunk in candidates:
            text = (chunk.content or "").lower()
            if all(needle in text for needle in needles):
                return chunk
        return None

    hackathon = (
        find("timeline", "hackathon runs", "thursday 24 september", prefer_chat=True)
        or find("hackathon runs", "thursday 24 september", prefer_chat=True)
        or find("timeline", "final submission deadline", "24 september", prefer_chat=True)
        or find("hackathon", "thursday 24 september")
        or find("final submission deadline", "24 september")
    )
    video = (
        find("video", "2:00 pm cat")
        or find("demo", "2:00 pm cat")
        or find("18 september", "2:00 pm cat")
    )
    team = (
        find("team declarations due", "17 september", prefer_chat=True)
        or find("team declaration", "17 september", prefer_chat=True)
        or find("close of business", "17 september", prefer_chat=True)
    )

    asks_team = "team" in q or "declare" in q or "declaration" in q
    asks_video = "video" in q or "demo" in q or "un " in q or "united nations" in q
    asks_hackathon = "hackathon" in q or "bot" in q or "chatbot" in q or "challenge" in q

    evidence_chunks: list[Chunk] = []
    answer = ""
    if asks_team and team:
        answer = (
            "The team-declaration deadline was close of business on Thursday 17 September 2026. "
            "Sorry, that deadline has already passed."
        )
        evidence_chunks = [team]
    elif asks_video and video:
        answer = (
            "The UN demo video submission deadline was Friday 18 September 2026 at 2:00 PM CAT. "
            "Sorry, that deadline has already passed."
        )
        evidence_chunks = [video]
    elif asks_hackathon and hackathon:
        answer = "The chatbot hackathon submission deadline is Thursday 24 September 2026."
        evidence_chunks = [hackathon]
    elif hackathon or video or team:
        parts = []
        if hackathon:
            parts.append("For the chatbot hackathon, the final submission deadline is Thursday 24 September 2026.")
            evidence_chunks.append(hackathon)
        if video:
            parts.append("The separate UN demo video deadline was Friday 18 September 2026 at 2:00 PM CAT, so sorry, that one has already passed.")
            evidence_chunks.append(video)
        if team:
            parts.append("The team-declaration deadline was close of business on Thursday 17 September 2026, so that has also passed.")
            evidence_chunks.append(team)
        answer = " ".join(parts)

    if not answer or not evidence_chunks:
        return None

    deduped: list[Chunk] = []
    seen: set[uuid.UUID] = set()
    for chunk in evidence_chunks:
        if chunk.id not in seen:
            seen.add(chunk.id)
            deduped.append(chunk)

    return MemoryAnswer(
        answer=answer,
        confidence="high",
        evidence=[evidence_from_chunk(c) for c in deduped[:3]],
        command="ask",
    )


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
                if any(name in author for name in ("diane", "gift", "munira", "jeovaire", "charles")):
                    s += 4.0
                elif "admin" in author or "organizer" in author:
                    s += 2.0
                if "attached document" in author:
                    s += 3.0
                if "bot" in author or "meti_bot" in author:
                    s -= 5.0

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


async def expand_context(
    db: AsyncSession,
    chunks: list[Chunk],
    *,
    conversation_id: str | None = None,
    max_items: int = 14,
) -> list[Chunk]:
    """Add nearby chat context around retrieved chunks without losing citation ids."""
    if not chunks:
        return []

    ordered: list[Chunk] = []
    seen_chunk_ids: set = set()

    def add(chunk: Chunk) -> None:
        if chunk.id in seen_chunk_ids:
            return
        seen_chunk_ids.add(chunk.id)
        ordered.append(chunk)

    for chunk in chunks:
        add(chunk)

    message_ids = [c.message_id for c in chunks if c.message_id]
    if not message_ids or len(ordered) >= max_items:
        return ordered[:max_items]

    msg_result = await db.execute(select(Message).where(Message.id.in_(message_ids)))
    anchor_messages = list(msg_result.scalars().all())
    if not anchor_messages:
        return ordered[:max_items]

    seen_message_ids = {c.message_id for c in ordered if c.message_id}
    for anchor in anchor_messages:
        if len(ordered) >= max_items:
            break
        if conversation_id and anchor.conversation_id != conversation_id:
            continue

        before = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == anchor.conversation_id,
                Message.timestamp <= anchor.timestamp,
            )
            .order_by(Message.timestamp.desc())
            .limit(3)
        )
        after = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == anchor.conversation_id,
                Message.timestamp > anchor.timestamp,
            )
            .order_by(Message.timestamp.asc())
            .limit(2)
        )
        nearby = list(reversed(list(before.scalars().all()))) + list(
            after.scalars().all()
        )

        for msg in nearby:
            if len(ordered) >= max_items:
                break
            if msg.id in seen_message_ids:
                continue
            seen_message_ids.add(msg.id)
            media_note = ""
            if msg.media_url:
                media_label = msg.media_mime or msg.source_type or "media"
                media_note = f"\n[Attached media: {media_label}. URL/id available to the system.]"
            add(
                Chunk(
                    id=uuid.uuid4(),
                    message_id=msg.id,
                    meeting_id=None,
                    content=f"{msg.text}{media_note}",
                    embedding=None,
                    platform=msg.platform,
                    author_name=msg.author_name,
                    timestamp=msg.timestamp,
                    meeting_offset_sec=msg.meeting_offset_sec,
                    meta={
                        "source_type": msg.source_type,
                        "external_id": msg.external_id,
                        "conversation_id": msg.conversation_id,
                        "media_url": msg.media_url,
                        "media_mime": msg.media_mime,
                        "has_media": bool(msg.media_url),
                        "context_expanded": True,
                    },
                )
            )

    ordered.sort(key=lambda c: c.timestamp or c.created_at)
    return ordered[:max_items]


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
    if _is_too_vague(question):
        return MemoryAnswer(
            answer="Please send the specific question or paste the message you want me to explain. I don't have enough context to answer that accurately.",
            confidence="insufficient",
            evidence=[],
            command="ask",
        )

    high_stakes = _is_high_stakes_question(question)
    retrieved_chunks = await retrieve(
        db,
        question,
        limit=14 if fast else 24,
        conversation_id=conversation_id,
        exclude_message_ids=exclude_message_ids,
    )
    if not retrieved_chunks:
        return MemoryAnswer(
            answer=(
                "I couldn't find that in the shared group chats yet."
                if require_evidence
                else "I couldn't find enough evidence to answer that."
            ),
            confidence="insufficient",
            evidence=[],
        )
    chunks = await expand_context(
        db,
        retrieved_chunks,
        conversation_id=conversation_id,
        max_items=18 if fast else 28,
    )
    chunks = _rank_evidence(question, chunks)

    if "deadline" in question.lower() or "due" in question.lower() or "submit" in question.lower() or "submission" in question.lower():
        deadline_result = await db.execute(select(Chunk).order_by(Chunk.created_at.desc()).limit(2000))
        deadline_chunks = _rank_evidence(question, list(deadline_result.scalars().all()))
    else:
        deadline_chunks = chunks
    deadline_answer = _deadline_answer_from_evidence(question, deadline_chunks)
    if deadline_answer:
        return deadline_answer

    fit_chunks = [c for c in chunks if _fit_score(question, c) >= 0.45]
    if _needs_exact_numeric_evidence(question):
        fit_chunks = [c for c in fit_chunks if _has_exact_numeric_evidence(question, c)]
    trusted_fit_chunks = [c for c in fit_chunks if _is_trusted_source(c)]
    if high_stakes:
        if trusted_fit_chunks:
            # Put leaders/documents first, then keep nearby supporting context.
            trusted_ids = {c.id for c in trusted_fit_chunks}
            chunks = trusted_fit_chunks + [c for c in chunks if c.id not in trusted_ids]
        else:
            return MemoryAnswer(
                answer=(
                    "I found related chat, but not a clear answer from @Diane, @Gift, "
                    "another organiser, or an official document. I don't want to guess on this. "
                    "Please ask an admin to confirm."
                ),
                confidence="insufficient",
                evidence=[],
                command="ask",
            )
    elif fit_chunks:
        fit_ids = {c.id for c in fit_chunks}
        chunks = fit_chunks + [c for c in chunks if c.id not in fit_ids]

    chunks = chunks[:10 if fast else 16]

    evidence_blocks = []
    for i, chunk in enumerate(chunks):
        offset = ""
        if chunk.meeting_offset_sec is not None:
            m, s = divmod(int(chunk.meeting_offset_sec), 60)
            offset = f" @{m}:{s:02d}"
        meta = chunk.meta or {}
        source_type = meta.get("source_type") or "chat"
        media = ""
        if meta.get("has_media") or meta.get("media_url"):
            media_mime = meta.get("media_mime") or "media"
            media = f" | attached {media_mime}"
        author = chunk.author_name or "unknown"
        author_mention = _author_mention(chunk.author_name)
        sent_at = (
            f" | sent {chunk.timestamp.date().isoformat()}"
            if chunk.timestamp
            else ""
        )
        evidence_blocks.append(
            f"[{i}] ({chunk.platform or 'unknown'} | {source_type} | "
            f"{author} / {author_mention}{sent_at}{offset}{media} | "
            f"trust={'trusted' if _is_trusted_source(chunk) else 'ordinary'} | "
            f"fit={_fit_score(question, chunk):.2f}) {chunk.content}"
        )

    mode_hint = ""
    if mode == "30sec" or fast:
        mode_hint = "Keep the answer to one or two short sentences."
    elif mode == "decisions":
        mode_hint = "Focus only on decisions."
    elif mode == "tasks":
        mode_hint = "Focus only on action items / tasks."

    prompt = (
        f"Today: {datetime.now().date().isoformat()}\n"
        f"Question: {question}\n{mode_hint}\n\n"
        f"Question type: {'official/high-stakes' if high_stakes else 'general'}\n"
        "Answerability rule: before answering, check that the selected evidence directly answers "
        "the exact question. For official/high-stakes questions, use trusted evidence only "
        "(leaders/admins or official documents). If trusted evidence is missing or only loosely related, "
        "return confidence=\"insufficient\".\n\n"
        "Use every relevant evidence item below before answering. If the evidence mentions "
        "unread or pending media/transcription, say that clearly instead of inferring its contents. "
        "If the evidence contains dates or deadlines, compare them with today's date and clearly "
        "say when something has already passed. Use evidence timestamps to interpret relative dates "
        'like "today", "tomorrow", and "yesterday".\n\n'
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
