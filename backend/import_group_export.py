"""Import one WhatsApp export into a configured group without clearing other groups.

Usage:
  python import_group_export.py "C:\\path\\to\\chat.txt" 120363429618850959@g.us
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, select

from app.core.group_profiles import profile_for
from app.db.session import SessionLocal, init_db
from app.db.models import Chunk, Message
from app.services.ingestion import ingest_messages
from ingest_whatsapp_export import parse_chat


async def main(chat_path: Path, group_id: str, *, replace: bool = False) -> None:
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
        if replace:
            # A supplied export is the authoritative history for this virtual
            # group. Delete only its prior imported messages and their chunks;
            # other groups and documents remain untouched.
            message_ids = select(Message.id).where(
                Message.conversation_id == group_id
            )
            await db.execute(delete(Chunk).where(Chunk.message_id.in_(message_ids)))
            await db.execute(delete(Message).where(Message.conversation_id == group_id))
            await db.commit()
        stored = await ingest_messages(db, parsed)
    action = "Replaced with" if replace else "Imported"
    print(f"{action} {len(stored)} messages in {profile['name']} ({group_id}).")


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4}:
        raise SystemExit(
            "Usage: python import_group_export.py <chat.txt> <group-id> [--replace]"
        )
    replace = len(sys.argv) == 4 and sys.argv[3] == "--replace"
    if len(sys.argv) == 4 and not replace:
        raise SystemExit("Unknown option. Use --replace to replace this group's prior import.")
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], replace=replace))
