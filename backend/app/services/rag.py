"""RAG retrieval + evidence-first answer generation."""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.group_profiles import is_group_admin, profile_for
from app.db.models import Chunk, Decision, Message
from app.schemas.memory import EvidenceItem, MemoryAnswer
from app.services.embeddings import embed_texts
from app.services.evidence import evidence_from_chunk, evidence_from_message
from app.services.llm import generate_json

SYSTEM_PROMPT = """You are the official UniPods METI AI Assistant Bot (in this cohort known as our community bot / meti_bot) for the UniPods METI AI Innovation Programme 2026 Cohort (supported by METI, UNDP, and timbuktoo).

YOUR PERSONALITY & TONE (EXACTLY AS METI_BOT):
- Direct, warm, welcoming, professional, and exceptionally helpful community assistant.
- Answer the user's specific question directly in the very first sentence. Never use throat-clearing boilerplate like "Based on the evidence provided...", "According to the records...", or "As an AI assistant...".
- Do NOT recite or list the four tracks or general programme overview unless the user specifically asks about the tracks, curriculum, or overview! Always address the exact topic asked.
- Structure responses cleanly using bullet points or numbered steps, with WhatsApp markdown bolding (*text*) for dates, deadlines, names, key terms, and URLs.
- Use natural, friendly emojis (😊, 🚀, 🙌, 🤖, 🌚, 🤞) appropriately to maintain an encouraging community atmosphere.
- Multilingual agility:
  * If the user writes in French, respond entirely and fluently in French (e.g. "Bonjour ! ...", "Voici les informations...").
  * If the user writes in Sesotho, respond in Sesotho.
  * If the user writes in Hausa, Arabic, Swahili, Portuguese, or another language, respond in that language.
  * If asked to translate, provide accurate, clean translations immediately.

CORE PROGRAMME KNOWLEDGE & CANONICAL FACTS:
1. Four Main Tracks:
   - Track 1: MIT Universal AI — self-paced foundational AI skills (Python, data analysis, ML, GenAI). 16 foundational modules are compulsory; vertical modules are optional. 100% free via program waiver (no payment or coupon code needed if accessed via the invite link). Enrolment is via individual link sent to applicant email from "MIT Learn". Only the applicant is officially enrolled and named on the certificate, but login credentials can be shared with team members so everyone learns. To view modules, click Dashboard (top-right), NOT Home. Support: uaisupport@mit.edu. Expected completion date: 18 October 2026.
   - Track 2: Wadhwani Ignite Full - Africa — 14-week entrepreneurship curriculum facilitated by Charles Bolton. Live class every Tuesday at 3:00 PM CAT; live coaching & Q&A every Thursday at 3:00 PM CAT. Platform: https://web.nen.wfglobal.org/en/login?mode=createAccount&source=student. Creating venture: only ONE person per team clicks "Create my venture", enters business name, industry, country, and city, then clicks "Add member" to add teammates (up to 5 members per team). Module 1 problem statement: max 350 characters, customer-focused, root cause, who will pay.
   - Track 3: Ethiopian AI Institute — intermediate to advanced AI virtual coursework starting late October 2026 (building on MIT). Needs Assessment Workshop: Wednesday, 23 September 2026, 10:00 AM - 11:30 AM CAT (East Africa Time 11:00 AM) on Teams: https://teams.microsoft.com/l/meetup-join/19%3ameeting_MjgxNmY4NGItZTZlMi00OTNmLTk2YzEtMjg0ZTdmYWJjM2Q4%40thread.v2/0?context=%7b%22Tid%22%3a%22b3e5db5e-2944-4837-99f5-7488ace54319%22%2c%22Oid%22%3a%2225f213f2-0e2f-4763-83fa-0d909a0e9701%22%7d
   - Track 4: In-person Addis Ababa Bootcamp — 50 strongest teams selected in late November 2026 (week of 23 Nov) by Ethiopian AI Institute. Bootcamp begins 1 December 2026 in Addis Ababa (1 person per selected team). Teams not selected are considered for the second bootcamp in February 2027. Leads to potential grant funding and timbuktoo Hubs linkage.
2. Official Programme Announcements & Policies:
   - Certificates vs Recommendation Letters: On 21 September, Gift Ntuli announced that after consultation with UNDP, participants who complete the programme will receive official certificates rather than recommendation letters. In needs assessments, teams should identify specific potential partners, and in-country UniPods will work to connect them.
   - In-Country UniPods: Local UniPods across 21+ countries will be reaching out to participants to provide country-level support.
   - Open Hours: "Ask Us Anything" sessions twice a month (alternating weeks) on Mondays with Gift Ntuli and Wednesdays with Diane at 3:00 PM CAT (14:00 WAT / 16:00 EAT).
3. Chatbot Hackathon:
   - Prize: $5,000 cash prize for ONE winning team, selected by a vote of the entire cohort.
   - Objective: Build a working chatbot that ingests group discussions, announcements, and recordings to answer member questions and FAQs.
   - Deliverables: Working chatbot (WhatsApp, Telegram, or Web interface like React) + source code repository access + short setup/maintenance notes.
   - Team Rules: Up to 5 members per team, must include at least one woman, cannot all be from the same country (group poll approved up to 2 from the same country). Team declaration deadline was 17 September by email to unipods.regional@undp.org (Subject: "UniPods Hackathon – Team Declaration"), but late declarations can still be emailed and confirmed with Diane.
   - Testing & Deployment: Bots are named [TeamName] BOT (e.g. SPARK BOT, JYMNS BOT, NEXUS BOT). Teams must inform Diane (+250 783 188 655) before deployment; testing is strictly rotational (one bot at a time in the group) with slots booked through 3 October. When a team's testing slot ends, the bot must be disconnected.
4. Essential Links & Recordings:
   - MIT Universal AI course: https://learn.mit.edu/universal-learning/ai
   - MIT Onboarding Recording (16 Sep): https://drive.google.com/file/d/1E5RrwULX8zSjwxHFSxiQzCTtp20ulYQ8/view
   - Wadhwani Welcome + Module 0: https://youtu.be/yVji4ZQECVw
   - Wadhwani Module 1 Class: https://youtu.be/6q4uPBO_sDc
   - Wadhwani Module 1 Coaching / Q&A: https://youtu.be/-6G7LXiu47o
   - Wadhwani Live Session Teams Link: https://teams.microsoft.com/meet/419860837373470?p=jYchWkDZnC4etsclnK (Meeting ID: 419 860 837 373 470, Passcode: g2Z7gc7Q)
   - Open Hours Teams Link: https://teams.microsoft.com/l/meetup-join/19%3ameeting_MjlkNWYyMjYtMGNhMi00NDM1LTlkNmYtOTZhYTU2MDU4MDc2%40thread.v2/0?context=%7B%22Tid%22%3A%22b3e5db5e-2944-4837-99f5-7488ace54319%22%2C%22Oid%22%3A%2225f213f2-0e2f-4763-83fa-0d909a0e9701%22%7D
   - Team Registration Google Sheet: https://docs.google.com/spreadsheets/d/15sAD53FA9LZXJ7EzOIzWLALTViaPz2_e/edit
   - Programme Email: unipods.regional@undp.org | Coordinator: Diane (+250 783 188 655) | MIT Support: uaisupport@mit.edu

STRICT ANTI-HALLUCINATION & ANTI-CLUTTER RULES:
- NEVER output citation codes, message indexes, or database markers like "(M389)", "(K74)", "(K109)", or "Answered before by...".
- NEVER repeat or quote fellow participants' chat banter, personal complaints, jokes, or names unless specifically asked about a person.
- NEVER loop robotic refusal phrases like "I don't have that in the programme materials, so I'd rather not guess."
- If an issue requires official admin approval or personal assistance (e.g. late team declaration approval, testing slot booking, individual login errors), provide the known policy warmly and direct them to contact Diane (+250 783 188 655) or email unipods.regional@undp.org.

Return JSON with keys:
  - "answer": Clean, polished answer formatted for WhatsApp (using markdown bolding and bullet points)
  - "confidence": "high" | "medium" | "low" | "insufficient"
  - "decision": (optional string if a formal decision was made, else null)
  - "reason": (optional string, else null)
  - "evidence_indices": list of 0-based integer indices of supporting evidence items
"""

TRUSTED_AUTHORS = ("diane", "gift", "munira", "jeovaire", "charles")
HIGH_STAKES_TERMS = (
    "deadline", "submit", "submission", "team", "teams", "declare", "declaration",
    "rule", "rules", "requirement", "required", "must", "eligible", "eligibility",
    "prize", "cash", "winner", "judge", "judging", "test", "testing", "deploy",
    "meeting", "session", "link", "recording", "email", "form", "course", "mit",
    "wadhwani", "payment", "funding", "grant", "certificate", "recommendation", "workshop",
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
    meta = chunk.meta or {}
    conversation_id = meta.get("conversation_id")
    # A configured group has its own supplied admin roster. Do not inherit the
    # hard-coded UniPods roster when answering for that group.
    if profile_for(conversation_id):
        return is_group_admin(
            conversation_id,
            author_id=str(meta.get("author_id") or ""),
            author_name=chunk.author_name,
        )
    author = (chunk.author_name or "").lower()
    shared_by = str(meta.get("shared_by") or "").lower()
    meeting_title = str(meta.get("meeting_title") or "").lower()
    doc_filename = str(meta.get("document_filename") or "").lower()
    return (
        any(name in author for name in TRUSTED_AUTHORS)
        or any(name in shared_by for name in TRUSTED_AUTHORS)
        or bool(meeting_title)
        or bool(doc_filename)
        or ("shared" in author and bool(meta.get("document_filename")))
        or any(k in author for k in ("coordinator", "lead", "undp", "admin", "organizer", "guideline", "info pack", "master knowledge", "programme"))
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
    # If the question is in French or another non-English language, let LLM handle it fluently
    if any(w in q for w in ("quand", "date limite", "delai", "délai", "soumission", "c'est", "est-ce", "bonjour", "salut", "merci", "ke kopa")):
        return None

    if "deadline" not in q and "due" not in q and "submit" not in q and "submission" not in q and "completion" not in q:
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

    mit_course = (
        find("18 october", "expected completion")
        or find("18 october", "mit")
        or find("october 18", "mit")
    )
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

    asks_mit = "mit" in q or "universal ai" in q or ("course" in q and "hackathon" not in q and "wadhwani" not in q)
    asks_team = "team" in q or "declare" in q or "declaration" in q
    asks_video = "video" in q or "demo" in q or "un " in q or "united nations" in q
    asks_hackathon = ("hackathon" in q or "chatbot" in q or "challenge" in q) and not asks_mit

    # MIT deadline — answer directly from system knowledge
    if asks_mit:
        answer = (
            "The expected completion date for the *MIT Universal AI* course is *18 October 2026*. 📅 "
            "This is the course deadline for all 16 compulsory foundational modules."
        )
        return MemoryAnswer(
            answer=answer,
            confidence="high",
            evidence=[evidence_from_chunk(mit_course)] if mit_course else [],
            command="ask",
        )

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
        # Strictly isolate configured groups. They must never answer using
        # another WhatsApp group's history or imported programme documents.
        same_conv = []
        other_chunks = []
        for c in chunks:
            meta = c.meta or {}
            c_conv = meta.get("conversation_id")
            if c_conv == conversation_id or (not c.message_id):  # Meeting/doc chunks have no message_id
                same_conv.append(c)
            else:
                other_chunks.append(c)
        chunks = same_conv if profile_for(conversation_id) else same_conv + other_chunks

    # Links are high-precision facts. Semantic similarity can otherwise rank a
    # generic programme announcement above the message that actually contains
    # the requested URL (for example, "Abaca Entrepreneurs platform"). When a
    # user asks for a link, first favour URL-bearing messages that repeat two
    # or more meaningful words from the request.
    import re

    asks_for_link = bool(re.search(r"\b(link|url|website|site|join)\b", query, re.I))
    if asks_for_link:
        stop_words = {
            "the", "and", "for", "with", "from", "that", "this", "what",
            "where", "which", "please", "could", "would", "can", "have",
            "about", "platform", "website", "link", "url", "site", "join",
        }
        terms = [
            term
            for term in re.findall(r"[a-z0-9]+", query.lower())
            if len(term) > 2 and term not in stop_words
        ]
        link_hits: list[tuple[int, Chunk]] = []
        for chunk in chunks:
            content = (chunk.content or "").lower()
            if not re.search(r"https?://", content):
                continue
            matches = sum(term in content for term in terms)
            if matches >= 2:
                link_hits.append((matches, chunk))
        if link_hits:
            link_hits.sort(
                key=lambda item: (item[0], item[1].timestamp or item[1].created_at),
                reverse=True,
            )
            return [chunk for _, chunk in link_hits[:limit]]

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
    if high_stakes and trusted_fit_chunks:
        trusted_ids = {c.id for c in trusted_fit_chunks}
        chunks = trusted_fit_chunks + [c for c in chunks if c.id not in trusted_ids]
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
        f"Instruction: Answer ONLY the specific question '{question}'. Do not give a general program overview or list other tracks.\n"
        "Answerability rule: before answering, check that the selected evidence directly answers "
        "the exact question. For official/high-stakes questions, use trusted evidence only "
        "(leaders/admins or official documents).\n\n"
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

    answer_text = data.get("answer") or ""

    # If LLM returned a note="extractive-match", the answer is the raw best-chunk content — use it directly
    # If LLM set confidence to insufficient, check if we still got a real answer_text worth returning
    if confidence == "insufficient":
        if answer_text and len(answer_text) > 30 and "couldn't" not in answer_text.lower():
            # LLM still gave a real answer even with low confidence — trust it
            confidence = "medium"
            if chunks:
                evidence = [evidence_from_chunk(chunks[0])]
        else:
            evidence = []
            answer_text = (
                "I couldn't find that in the shared group chats yet."
                if require_evidence
                else "I couldn't find enough evidence to answer that."
            )
    elif not answer_text or len(answer_text) < 5:
        # LLM returned empty answer — use the top evidence chunk as extractive answer
        if chunks:
            answer_text = chunks[0].content or "I couldn't find enough evidence to answer that."
        else:
            answer_text = "I couldn't find enough evidence to answer that."

    # Strip any raw evidence block that leaked into the answer (starts with "[0]" or "/ @")
    if answer_text.startswith("/ @") or (len(answer_text) > 4 and answer_text[1] == "/" and answer_text[0] == " "):
        answer_text = answer_text.split(")", 1)[-1].strip() if ")" in answer_text else answer_text

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
