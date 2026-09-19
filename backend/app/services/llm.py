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
    timeout = 15.0 if fast else max(settings.llm_timeout_seconds, 25.0)
    retries = 1 if fast else max(settings.llm_retries, 1)
    max_tokens = 350 if fast else settings.llm_max_tokens

    if settings.nvidia_api_key:
        try:
            return await _nvidia(
                prompt, system, timeout=timeout, retries=retries, max_tokens=max_tokens
            )
        except Exception as exc:
            print(f"[LLM] NVIDIA failed: {repr(exc)}")
    if settings.groq_api_key and not fast:
        try:
            return await _groq(prompt, system)
        except Exception as exc:
            print(f"[LLM] Groq failed: {exc}")
    if settings.gemini_api_key and not fast:
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


async def _groq(prompt: str, system: str | None) -> str:
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
        max_tokens=settings.llm_max_tokens,
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
    """Evidence-first extractive fallback — fast path for WhatsApp/Teams."""
    import re

    blocks = re.findall(
        r"\[(\d+)\]\s*\(([^|]*)\|\s*([^)]*)\)\s*(.+?)(?=\n\[\d+\]|\Z)",
        prompt,
        flags=re.S,
    )
    if not blocks:
        return json.dumps(
            {
                "answer": (
                    "I couldn't find enough evidence to answer that."
                ),
                "confidence": "insufficient",
                "decision": None,
                "reason": None,
                "evidence_indices": [],
            }
        )

    ranked = []
    for idx, _platform, author_offset, content in blocks:
        text = content.strip()
        score = 1
        lower = text.lower()
        if any(
            w in lower
            for w in ("agreed", "decided", "selected", "confirmed", "deadline", "schedule", "note", "remember", "submit", "meeting", "venue", "link")
        ):
            score += 3
        ranked.append((score, int(idx), author_offset.strip(), text))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    best = ranked[0]
    author = best[2]
    content = best[3]
    answer = f"{author}: {content}" if author else content

    return json.dumps(
        {
            "answer": answer,
            "confidence": "medium",
            "decision": None,
            "reason": None,
            "evidence_indices": [r[1] for r in ranked[:3]],
            "note": "extractive-fallback",
        }
    )
