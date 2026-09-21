"""LLM client — NVIDIA NIM primary, Groq / Gemini fallback. Optimized for chat latency."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import settings


async def generate(
    prompt: str,
    system: str | None = None,
    *,
    fast: bool = False,
) -> str:
    timeout = 8.0 if fast else max(settings.llm_timeout_seconds, 30.0)
    retries = 1
    max_tokens = 350 if fast else settings.llm_max_tokens

    if settings.nvidia_api_key:
        try:
            return await _nvidia(
                prompt, system, timeout=timeout, retries=retries, max_tokens=max_tokens
            )
        except Exception as exc:
            print(f"[LLM] NVIDIA failed: {repr(exc)}")
    if settings.groq_api_key:
        try:
            return await _groq(prompt, system, max_tokens=max_tokens)
        except Exception as exc:
            print(f"[LLM] Groq failed: {exc}")
    if settings.gemini_api_key:
        try:
            return await _gemini(prompt, system)
        except Exception as exc:
            print(f"[LLM] Gemini failed: {exc}")
    return _offline_fallback(prompt)


async def generate_json(
    prompt: str,
    system: str | None = None,
    *,
    fast: bool = False,
) -> dict[str, Any]:
    text = await generate(prompt, system, fast=fast)
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try to find JSON object substring { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    if text and not text.startswith("{"):
        return {
            "answer": text,
            "confidence": "medium",
            "evidence_indices": [0],
        }

    # Last resort fallback if parsing completely fails
    return json.loads(_offline_fallback(prompt))


async def _nvidia(
    prompt: str,
    system: str | None,
    *,
    timeout: float,
    retries: int,
    max_tokens: int,
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = settings.nvidia_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "model": settings.nvidia_model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(attempts):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last_error = exc
                continue
            if resp.status_code in {429, 500, 502, 503}:
                last_error = httpx.HTTPStatusError(
                    f"NVIDIA {resp.status_code}: {resp.text[:200]}",
                    request=resp.request,
                    response=resp,
                )
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
    if last_error:
        raise last_error
    raise RuntimeError("NVIDIA request failed")


async def _groq(prompt: str, system: str | None, *, max_tokens: int | None = None) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        temperature=0.2,
        max_tokens=max_tokens or settings.llm_max_tokens,
    )
    return resp.choices[0].message.content or ""


async def _gemini(prompt: str, system: str | None) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=system,
    )
    resp = await model.generate_content_async(prompt)
    return resp.text or ""


def _offline_fallback(prompt: str) -> str:
    """
    Evidence-first extractive fallback with question-relevance scoring.
    Only returns confidence='high'/'medium' if the evidence actually answers
    the question. If there is no good match, returns confidence='insufficient'
    so the bot remains silent in group chats instead of posting unrelated quotes.
    """
    import re

    # Extract user question from prompt
    q_match = re.search(r"Question:\s*(.+?)(?:\n|$)", prompt, flags=re.I)
    question = q_match.group(1).strip() if q_match else ""
    q_lower = question.lower()
    high_stakes_terms = (
        "deadline", "submit", "submission", "team", "teams", "declare", "declaration",
        "rule", "rules", "requirement", "required", "must", "eligible", "eligibility",
        "prize", "cash", "winner", "judge", "judging", "test", "testing", "deploy",
        "meeting", "session", "link", "recording", "email", "form", "course", "mit",
        "wadhwani", "payment", "funding", "grant",
    )
    high_stakes = any(term in q_lower for term in high_stakes_terms)

    blocks = re.findall(
        r"\[(\d+)\]\s*\(([^|]*)\|\s*([^)]*)\)\s*(.+?)(?=\n\[\d+\]|\Z)",
        prompt,
        flags=re.S,
    )
    if not blocks or not question:
        return json.dumps(
            {
                "answer": "I couldn't find enough evidence to answer that.",
                "confidence": "insufficient",
                "decision": None,
                "reason": None,
                "evidence_indices": [],
            }
        )

    # ── Special handler: hackathon info ─────────────────────────────────────
    hackathon_keywords = ("hackathon", "unipod", "meti", "competition", "programme", "cohort", "prize", "submission", "deadline", "challenge")
    # Disabled: this used old demo/team facts. The fallback must answer only
    # from retrieved evidence after the real WhatsApp export is imported.
    if False and any(k in q_lower for k in hackathon_keywords):
        hackathon_answer = (
            "Here's what we know about our hackathon:\n\n"
            "🏆 *METI UniPods AI Innovation Programme — Cohort 1*\n"
            "• Prize: $5,000 for the winning team\n"
            "• Deadline: September 24, 2026\n"
            "• Submission: Working chatbot + source code + setup notes\n"
            "• Platform: WhatsApp-based AI community memory bot\n\n"
            "Our team (JOTDS):\n"
            "• Shema Owen — Full-Stack Developer\n"
            "• Joel — Architect & Software Engineer\n"
            "• Deborah — Software Engineer & QA\n"
            "• Reitumetse — Economist, AI & Full-Stack\n"
            "• Kgosi — Business Development & Project Management"
        )
        return json.dumps(
            {
                "answer": hackathon_answer,
                "confidence": "high",
                "decision": None,
                "reason": "Hackathon guidelines and team context",
                "evidence_indices": [0],
            }
        )

    # ── Special handler: team members / roster ───────────────────────────────
    if False and any(k in q_lower for k in ("who are", "who is", "members", "our team", "participants")):
        member_names = ["Shema", "Owen", "Joel", "joe", "Deborah", "Kgosi", "Reitumetse"]
        matched_indices = []
        found_names = set()
        for idx, _platform, author_offset, content in blocks:
            for name in member_names:
                if name.lower() in content.lower():
                    found_names.add(name)
                    matched_indices.append(int(idx))

        if len(found_names) >= 2 or "team" in prompt.lower():
            team_answer = (
                "Our team members are:\n"
                "• Shema Owen (Rwanda) — Full-Stack Developer\n"
                "• Joel / joe (Rwanda) — Architect & Software Engineer\n"
                "• Deborah (Rwanda) — Software Engineer & QA\n"
                "• Reitumetse Sehloho (Lesotho) — Economist, AI & Full-Stack\n"
                "• Kgosi (Botswana) — Business Development & Project Management"
            )
            return json.dumps(
                {
                    "answer": team_answer,
                    "confidence": "high",
                    "decision": "Team formation",
                    "reason": "Team introduction messages in group chat",
                    "evidence_indices": list(dict.fromkeys(matched_indices))[:3] if matched_indices else [0],
                }
            )

    # ── Question keyword extraction ──────────────────────────────────────────
    stop_words = {
        "what", "who", "when", "where", "why", "how", "did", "do", "does", "is",
        "are", "was", "were", "the", "a", "an", "in", "on", "at", "for", "to",
        "of", "and", "or", "our", "we", "they", "them", "this", "that", "there",
        "can", "could", "should", "would", "please", "tell", "me", "about", "more",
        "show", "give", "know", "just", "like", "need", "want", "have", "has",
    }
    clean_q = re.sub(r"[^\w\s]", " ", q_lower)
    q_tokens = [w for w in clean_q.split() if len(w) > 2 and w not in stop_words]

    # Normalised question text for similarity check
    q_norm = " ".join(sorted(q_tokens))

    scored = []
    for idx, _platform, author_offset, content in blocks:
        text = content.strip()
        text_lower = text.lower()
        meta_lower = author_offset.lower()
        trusted = (
            "trust=trusted" in meta_lower
            or any(name in meta_lower for name in ("diane", "gift", "munira", "jeovaire", "charles"))
            or "attached document" in meta_lower
        )
        if "bot" in meta_lower or "meti_bot" in meta_lower:
            continue
        if high_stakes and not trusted:
            continue

        # ── Skip chunks that ARE questions themselves ────────────────────────
        # A chunk ending in '?' is a question from a user, not an answer.
        text_stripped = text_lower.rstrip()
        if text_stripped.endswith("?"):
            continue

        # ── Skip chunks that are near-identical to the question ─────────────
        # e.g. the ingested message "tell me more about hackathon?" should not
        # be returned as the answer to "tell me more about hackathon?"
        clean_chunk = re.sub(r"[^\w\s]", " ", text_lower)
        chunk_tokens = [w for w in clean_chunk.split() if len(w) > 2 and w not in stop_words]
        chunk_norm = " ".join(sorted(chunk_tokens))
        if q_tokens and chunk_tokens:
            overlap = len(set(q_tokens) & set(chunk_tokens))
            similarity = overlap / max(len(set(q_tokens)), len(set(chunk_tokens)), 1)
            if similarity >= 0.7:  # 70%+ overlap → chunk IS the question → skip
                continue

        score = 0

        # Score based on overlap with question keywords
        for t in q_tokens:
            if t in text_lower:
                score += 3
            elif len(t) >= 4 and any(w.startswith(t[:4]) for w in text_lower.split()):
                score += 1

        # Boost for key domain words
        for kw in ("deadline", "date", "time", "meeting", "prize", "cash", "github",
                   "rules", "submission", "submit", "hackathon", "unipod"):
            if kw in q_lower and kw in text_lower:
                score += 4
        if trusted:
            score += 4

        scored.append((score, int(idx), author_offset.strip(), text))

    if not scored:
        return json.dumps(
            {
                "answer": "I couldn't find enough evidence to answer that.",
                "confidence": "insufficient",
                "decision": None,
                "reason": None,
                "evidence_indices": [],
            }
        )

    scored.sort(key=lambda x: -x[0])
    best_score, best_idx, best_author, best_text = scored[0]

    # If the overlap score is too low, evidence is not relevant → stay silent
    if best_score < 3:
        return json.dumps(
            {
                "answer": "I couldn't find enough evidence to answer that.",
                "confidence": "insufficient",
                "decision": None,
                "reason": None,
                "evidence_indices": [],
            }
        )

    # Return the best matching chunk as the answer (without restating the author prefix)
    return json.dumps(
        {
            "answer": best_text,
            "confidence": "high" if best_score >= 6 else "medium",
            "decision": None,
            "reason": None,
            "evidence_indices": [best_idx],
            "note": "extractive-match",
        }
    )

