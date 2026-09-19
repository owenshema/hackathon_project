"""Command parser for /ask /catchup /decisions /tasks /changed /meeting /find."""

from dataclasses import dataclass
from typing import Optional


COMMANDS = {
    "ask",
    "catchup",
    "decisions",
    "tasks",
    "changed",
    "meeting",
    "find",
}


@dataclass
class ParsedCommand:
    command: Optional[str]
    query: str
    is_command: bool


def parse_command(text: str) -> ParsedCommand:
    raw = (text or "").strip()
    if not raw:
        return ParsedCommand(command=None, query="", is_command=False)

    if raw.startswith("/"):
        parts = raw[1:].split(maxsplit=1)
        name = parts[0].lower().lstrip("/")
        rest = parts[1] if len(parts) > 1 else ""
        if name in COMMANDS:
            return ParsedCommand(command=name, query=rest.strip(), is_command=True)

    # Soft aliases (natural language)
    lower = raw.lower()
    if lower.startswith("catch me up") or "what did i miss" in lower:
        return ParsedCommand(command="catchup", query=raw, is_command=True)
    if lower.startswith("what changed"):
        return ParsedCommand(command="changed", query=raw, is_command=True)

    return ParsedCommand(command="ask", query=raw, is_command=False)
