"""Import one WhatsApp export into a configured group without clearing other groups.

Usage:
  python import_group_export.py "C:\\path\\to\\chat.txt" 120363429618850959@g.us
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.core.group_profiles import profile_for
from app.db.session import SessionLocal, init_db
from app.services.ingestion import ingest_messages
from ingest_whatsapp_export import parse_chat


async def main(chat_path: Path, group_id: str) -> None:
    if not chat_path.is_file():
        raise FileNotFoundError(chat_path)
    profile = profile_for(group_id)
    if not profile:
        raise ValueError(f"No configured group profile for {group_id}")

    parsed = parse_chat(chat_path, chat_path.parent)
    for index, message in enumerate(parsed, start=1):
        message.conversation_id = group_id
        message.external_id = f"group-export-{group_id.replace('@', '_')}-{index:06d}"
        message.metadata.update(
            {
                "is_group": True,
                "group_name": profile["name"],
                "group_profile": group_id,
                "strict_isolation": True,
            }
        )

    await init_db()
    async with SessionLocal() as db:
        stored = await ingest_messages(db, parsed)
    print(f"Imported {len(stored)} messages into {profile['name']} ({group_id}).")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python import_group_export.py <chat.txt> <group-id>")
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
