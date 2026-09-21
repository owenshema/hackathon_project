"""Ingest a WhatsApp export folder as the only bot memory source.

Usage:
    python ingest_whatsapp_export.py "../WhatsApp Chat - UniPods METI AI Program 2026 Cohort"
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from sqlalchemy import delete

from app.db.models import ActionItem, Chunk, Decision, Meeting, Message
from app.db.session import SessionLocal, init_db
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages


CAT = timezone(timedelta(hours=2))
MESSAGE_RE = re.compile(
    r"^\u200e?\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}), "
    r"(?P<time>\d{1,2}:\d{2}:\d{2})\u202f?(?P<ampm>AM|PM)\] "
    r"(?P<author>.*?): (?P<text>.*)$"
)
ATTACHED_RE = re.compile(r"<attached:\s*(?P<filename>[^>]+)>")
SYSTEM_PHRASES = (
    "messages and calls are end-to-end encrypted",
    "joined using a group link",
    "created this group",
    "added ",
    "left",
    "changed this group's",
    "changed the group description",
    "you joined using a group link",
)
BOT_AUTHOR_PATTERNS = (
    "meti_bot",
    "podpal bot",
    "askback",
    "wise-bot",
)


def clean_text(value: str) -> str:
    return (
        value.replace("\u200e", "")
        .replace("\u202f", " ")
        .replace("\ufeff", "")
        .strip()
    )


def clean_author(value: str) -> str:
    author = clean_text(value)
    if author.startswith("~"):
        author = author[1:].strip()
    return author or "Unknown"


def parse_timestamp(date_part: str, time_part: str, ampm: str) -> datetime:
    month, day, year = [int(x) for x in date_part.split("/")]
    if year < 100:
        year += 2000
    hour, minute, second = [int(x) for x in time_part.split(":")]
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, second, tzinfo=CAT)


def should_skip(author: str, text: str) -> bool:
    author_lower = clean_author(author).lower()
    if any(pattern in author_lower for pattern in BOT_AUTHOR_PATTERNS):
        return True
    lower = clean_text(text).lower()
    if not lower:
        return True
    if lower == "this message was deleted.":
        return True
    return any(phrase in lower for phrase in SYSTEM_PHRASES)


def parse_chat(chat_path: Path, export_dir: Path) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        author = clean_author(current["author"])
        text = clean_text("\n".join(current["lines"]))
        if should_skip(author, text):
            current = None
            return

        attachment = ATTACHED_RE.search(text)
        media_url = None
        media_mime = None
        source_type = SourceType.CHAT
        if attachment:
            filename = clean_text(attachment.group("filename"))
            media_path = export_dir / filename
            if media_path.exists():
                media_url = str(media_path)
                media_mime = mimetypes.guess_type(media_path.name)[0]
            if media_mime and media_mime.startswith("audio/"):
                source_type = SourceType.VOICE
            text = ATTACHED_RE.sub(f"[attached file: {filename}]", text).strip()

        messages.append(
            NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=source_type,
                external_id=f"whatsapp-export-{current['index']:05d}",
                conversation_id="UniPods METI AI Program 2026 Cohort",
                author_id=author.lower().replace(" ", "_")[:240],
                author_name=author,
                text=text,
                timestamp=current["timestamp"].isoformat(),
                media_url=media_url,
                media_mime=media_mime,
                metadata={
                    "is_group": True,
                    "group_name": "UniPods METI AI Program 2026 Cohort",
                    "source_export": str(export_dir),
                },
            )
        )
        current = None

    for index, raw_line in enumerate(chat_path.read_text(encoding="utf-8", errors="replace").splitlines()):
        match = MESSAGE_RE.match(raw_line)
        if match:
            flush()
            current = {
                "index": index,
                "timestamp": parse_timestamp(
                    match.group("date"), match.group("time"), match.group("ampm")
                ),
                "author": match.group("author"),
                "lines": [match.group("text")],
            }
        elif current:
            current["lines"].append(raw_line)
    flush()
    return messages


def attachment_shares(messages: list[NormalizedMessage]) -> dict[str, NormalizedMessage]:
    shares: dict[str, NormalizedMessage] = {}
    for message in messages:
        media_url = message.media_url
        if not media_url:
            continue
        shares[Path(media_url).name] = message
    return shares


def chunks(text: str, size: int = 1800, overlap: int = 180) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    out = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + size)
        out.append(clean[start:end])
        if end == len(clean):
            break
        start = max(0, end - overlap)
    return out


def extract_pdf_text(path: Path) -> list[tuple[str, str]]:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        return [("PDF import error", f"Could not read {path.name}: {exc}")]

    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages.append((f"page {i}", text))
    return pages


def extract_pptx_text(path: Path) -> list[tuple[str, str]]:
    slides: list[tuple[str, str]] = []
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(
                n
                for n in archive.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            for i, name in enumerate(names, start=1):
                root = ElementTree.fromstring(archive.read(name))
                text = " ".join(
                    t.text.strip()
                    for t in root.findall(".//a:t", ns)
                    if t.text and t.text.strip()
                )
                if text:
                    slides.append((f"slide {i}", text))
    except Exception as exc:
        slides.append(("PPTX import error", f"Could not read {path.name}: {exc}"))
    return slides


def parse_documents(
    export_dir: Path, shares: dict[str, NormalizedMessage]
) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    doc_paths = sorted(
        p for p in export_dir.iterdir() if p.suffix.lower() in {".pdf", ".pptx"}
    )
    for doc_index, path in enumerate(doc_paths, start=1):
        share = shares.get(path.name)
        shared_by = share.author_name if share else "Unknown"
        shared_at = share.timestamp if share else datetime.now(timezone.utc).isoformat()
        if path.suffix.lower() == ".pdf":
            sections = extract_pdf_text(path)
        else:
            sections = extract_pptx_text(path)

        for section, text in sections:
            for part_index, part in enumerate(chunks(text), start=1):
                messages.append(
                    NormalizedMessage(
                        platform=Platform.UPLOAD,
                        source_type=SourceType.DOCUMENT,
                        external_id=f"document-{doc_index:03d}-{section}-{part_index}",
                        conversation_id=f"document:{path.name}",
                        author_id=f"document_shared_by_{clean_author(shared_by).lower().replace(' ', '_')[:180]}",
                        author_name=f"{shared_by} shared {path.name}",
                        text=f"{path.name} ({section}), shared by {shared_by}: {part}",
                        timestamp=shared_at,
                        media_url=str(path),
                        media_mime=mimetypes.guess_type(path.name)[0],
                        metadata={
                            "document_filename": path.name,
                            "shared_by": shared_by,
                            "page_or_section": section,
                            "source_export": str(export_dir),
                        },
                    )
                )
    return messages


def parse_image_attachments(
    export_dir: Path, shares: dict[str, NormalizedMessage]
) -> list[NormalizedMessage]:
    messages: list[NormalizedMessage] = []
    image_paths = sorted(
        p for p in export_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    for i, path in enumerate(image_paths, start=1):
        share = shares.get(path.name)
        if not share:
            continue
        caption = clean_text(ATTACHED_RE.sub("", share.text or ""))
        if not caption:
            caption = "Image/sticker attachment. Text inside the image was not OCR-read."
        messages.append(
            NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=SourceType.DOCUMENT,
                external_id=f"image-attachment-{i:03d}",
                conversation_id=f"image:{path.name}",
                author_id=f"image_shared_by_{clean_author(share.author_name).lower().replace(' ', '_')[:180]}",
                author_name=f"{share.author_name} shared image {path.name}",
                text=f"{path.name}, shared by {share.author_name}: {caption}",
                timestamp=share.timestamp,
                media_url=str(path),
                media_mime=mimetypes.guess_type(path.name)[0],
                metadata={
                    "image_filename": path.name,
                    "shared_by": share.author_name,
                    "source_export": str(export_dir),
                    "ocr_status": "not_available",
                },
            )
        )
    return messages


async def ingest_export(export_dir: Path) -> None:
    chat_path = export_dir / "_chat.txt"
    if not chat_path.exists():
        raise FileNotFoundError(f"Missing WhatsApp chat file: {chat_path}")

    await init_db()
    async with SessionLocal() as db:
        print("[1/4] Clearing all existing bot memory...")
        for table in (Chunk, Message, Decision, ActionItem, Meeting):
            await db.execute(delete(table))
        await db.commit()

        print("[2/4] Parsing WhatsApp export chat...")
        chat_messages = parse_chat(chat_path, export_dir)
        print(f"  -> Parsed {len(chat_messages)} real chat messages.")

        print("[3/4] Extracting attached PDF/PPTX knowledge...")
        shares = attachment_shares(chat_messages)
        document_messages = parse_documents(export_dir, shares)
        image_messages = parse_image_attachments(export_dir, shares)
        print(f"  -> Extracted {len(document_messages)} document chunks.")
        print(f"  -> Indexed {len(image_messages)} image attachment references/captions.")

        print("[4/4] Ingesting export into memory...")
        stored = await ingest_messages(db, chat_messages + document_messages + image_messages)
        print(f"  -> Ingested {len(stored)} total messages/chunks from export only.")


if __name__ == "__main__":
    folder = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("../WhatsApp Chat - UniPods METI AI Program 2026 Cohort")
    )
    asyncio.run(ingest_export(folder.resolve()))
