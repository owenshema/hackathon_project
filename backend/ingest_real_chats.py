"""
Ingest real WhatsApp team chat history + Hackathon Guidelines into UniPods Memory DB
and clear out all mock/demo data.
"""

import asyncio
from datetime import datetime, timezone
from sqlalchemy import delete, select
from app.db.session import SessionLocal, init_db
from app.db.models import Message, Chunk, Decision, ActionItem, Meeting
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages
from app.services.meetings import ingest_transcript_file
from app.services.extraction import extract_from_texts

# Real WhatsApp team chats
RAW_CHATS = [
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T20:01:53+02:00",
        "text": "Hello @all once again every one is free to share their ideas on the task. Thank you"
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-16T20:02:36+02:00",
        "text": "Hi. Kgosi. Botswana. Business Development and Project Management"
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T20:20:37+02:00",
        "text": "My name is Shema Owen from Rwanda full-stack engineer, software developer. Joel and Deborah are still at work but anytime they will join."
    },
    {
        "author": "Deborah",
        "author_id": "deborah_rwanda",
        "time": "2026-09-16T21:01:32+02:00",
        "text": "Hello I am Deborah from Rwanda, software engineer, QA."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T21:36:55+02:00",
        "text": "What time are you available and we make a google meet @all"
    },
    {
        "author": "joe",
        "author_id": "+250789201681",
        "time": "2026-09-16T21:37:04+02:00",
        "text": "Hello everyone @all am Joel software engineer and architect from Rwanda"
    },
    {
        "author": "joe",
        "author_id": "+250789201681",
        "time": "2026-09-16T21:41:16+02:00",
        "text": "For me right now is good"
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T22:43:52+02:00",
        "text": "POLL: Time for our meeting. Option 1: 10:30pm CAT (1 vote), Option 2: 11:00pm CAT (1 vote)"
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T22:56:31+02:00",
        "text": "Meeting link: https://meet.google.com/ibg-ehky-pfr hello you can join the meeting"
    },
    {
        "author": "Deborah",
        "author_id": "deborah_rwanda",
        "time": "2026-09-16T23:01:52+02:00",
        "text": "Joining Google Meet now..."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-16T23:23:37+02:00",
        "text": "Shared UniPods Hackathon Guidelines.pdf document with the team."
    },
    {
        "author": "Reitumetse (T&R Analytics)",
        "author_id": "reitumetse_lesotho",
        "time": "2026-09-17T00:23:19+02:00",
        "text": "Hi everyone. I’m Reitumetse from Lesotho, an Economist, AI & Business Solutions specialist, and Full-Stack Developer. Looking forward to working with you all. I am also aware that I missed yesterday's meeting. I sincerely apologise for that."
    },
    {
        "author": "joe",
        "author_id": "+250789201681",
        "time": "2026-09-17T00:25:09+02:00",
        "text": "It's fine we understand. In the morning we will update you on what we discussed and the action plan on how we want to proceed."
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-17T08:17:30+02:00",
        "text": "Hello guys, my internet was cut last night. Apologies. We will be waiting for the update. Thanks!"
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-17T10:37:58+02:00",
        "text": "Good morning @all. 11:00 am CAT we have a meeting if you are available you can join."
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-17T10:39:12+02:00",
        "text": "In a workshop right now. Send meeting notes. Don't forget to send team name and members to UniPod."
    },
    {
        "author": "Reitumetse (T&R Analytics)",
        "author_id": "reitumetse_lesotho",
        "time": "2026-09-17T11:07:56+02:00",
        "text": "Is our meeting still on? I just got out of one of my meetings this side."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-17T13:20:27+02:00",
        "text": "Hello everyone please share your GitHub usernames for repository access."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-17T13:46:34+02:00",
        "text": "I have sent you all the GitHub invitations. Others can accept the invitation and we start with the implementation. In short time I will share the documentation."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-17T14:18:35+02:00",
        "text": "Is there anyone who knows how we are going to submit team names?"
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-17T14:19:14+02:00",
        "text": "Check on the group there, they said submission of team name and members is by email to UniPod."
    },
    {
        "author": "Reitumetse (T&R Analytics)",
        "author_id": "reitumetse_lesotho",
        "time": "2026-09-17T14:20:28+02:00",
        "text": "My Github username is: byte0107, full name Reitumetse Sehloho. I received the invitation and accepted."
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-18T18:26:21+02:00",
        "text": "Hello guys. We need to start developing the bot well in time. Share link or something of the work so far."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-18T18:47:18+02:00",
        "text": "We started developing yesterday, me and Joel. If you are around you can join the meeting: https://meet.google.com/jcj-vkiv-wxa"
    },
    {
        "author": "Kgosi",
        "author_id": "kgosi_botswana",
        "time": "2026-09-18T18:48:33+02:00",
        "text": "Around 21:00 I will be arriving. I will make contact with you."
    },
    {
        "author": "Shema Owen",
        "author_id": "+250782972679",
        "time": "2026-09-19T03:43:01+02:00",
        "text": "Don't mind the deleted messages, I was testing. You can check on GitHub there are updates!"
    }
]

HACKATHON_GUIDELINES = """00:00 Guidelines - CHATBOT HACKATHON: METI UniPods AI Innovation Programme, Cohort 1. $5,000 cash prize.
00:10 Problem - Our group has grown large, and it has become hard to keep up with every message. Due to high chat traffic people miss messages and end up asking questions that have already been addressed. People miss meetings and cannot rewatch call recordings. Information gets lost across chats and calls.
00:25 Challenge - Build a chatbot that makes sense of all this data, calls, chats, and more, and responds to people directly, so the group stays informed and connected despite the volume.
00:40 Submission - What to submit: A working chatbot (not just a slide deck or mockup), access to source code or repository, and short setup notes so anyone can run and maintain it.
00:55 TeamRules - Teams of up to 5 people. Team members cannot all be from the same country. Each team must include at least one woman. Announce team name and members by close of business, Thursday 17 September 2026.
01:10 Timeline - Team declarations due close of business Thursday 17 September 2026 by email with Subject 'UniPods Hackathon' indicating team members and countries of origin. Hackathon runs Friday 18 September to Thursday 24 September 2026. Judging done by whole group for $5,000 cash prize."""


async def run_clean_ingest():
    await init_db()
    async with SessionLocal() as db:
        print("[1/4] Clearing all existing mock and dummy data...")
        await db.execute(delete(Chunk))
        await db.execute(delete(Message))
        await db.execute(delete(Decision))
        await db.execute(delete(ActionItem))
        await db.execute(delete(Meeting))
        await db.commit()
        print("  -> Database tables cleared cleanly.")

        print("[2/4] Ingesting real WhatsApp team conversations...")
        normalized_messages = []
        for i, chat in enumerate(RAW_CHATS):
            nm = NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=SourceType.CHAT,
                external_id=f"unipod-chat-{i+1}",
                conversation_id="UNIPOD TASK GROUP",
                author_id=chat["author_id"],
                author_name=chat["author"],
                text=chat["text"],
                timestamp=datetime.fromisoformat(chat["time"]).isoformat(),
                metadata={
                    "is_group": True,
                    "group_name": "UNIPOD TASK GROUP",
                    "reply_kind": "group"
                }
            )
            normalized_messages.append(nm)

        stored_msgs = await ingest_messages(db, normalized_messages)
        print(f"  -> Ingested and embedded {len(stored_msgs)} real WhatsApp messages into PostgreSQL!")

        print("[3/4] Ingesting UniPods Hackathon Guidelines...")
        meeting = await ingest_transcript_file(
            db,
            title="UniPods Hackathon Guidelines",
            content=HACKATHON_GUIDELINES,
            filename="UniPods_Hackathon_Guidelines.txt"
        )
        print(f"  -> Ingested guidelines as knowledge source (id={meeting.id})!")

        print("[4/4] Extracting structured decisions and tasks from real chats...")
        result = await extract_from_texts(db, texts=[f"{m.author_name}: {m.text}" for m in stored_msgs], evidence_messages=stored_msgs)
        print(f"  -> Extracted {len(result.decisions)} decisions and {len(result.action_items)} action items!")

        print("\nAll real conversations and guidelines have been embedded and stored successfully!")

if __name__ == "__main__":
    asyncio.run(run_clean_ingest())
