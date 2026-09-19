"""
Master ingestion script:
1. Ingests the official METI UniPods AI Innovation Programme Information Pack (PDF).
2. Ingests the full UniPods Hackathon Guidelines with all rules, criteria, and prize breakdown.
3. Ingests the entire UniPods METI AI Program 2026 Cohort community WhatsApp history (all official announcements, Q&A, schedules, recordings links).
4. Ingests the team's internal conversations (UNIPOD TASK GROUP).
5. Populates structured decisions, deadlines, links, and action items.
"""

import asyncio
from datetime import datetime, timezone
from sqlalchemy import delete, select
from app.db.session import SessionLocal, init_db
from app.db.models import Message, Chunk, Decision, ActionItem, Meeting
from app.schemas.memory import NormalizedMessage, Platform, SourceType
from app.services.ingestion import ingest_messages
from app.services.meetings import ingest_transcript_file

INFO_PACK_CONTENT = """00:00 About - METI UniPods AI Innovation Programme: Information Pack for Selected Teams (Cohort 1).
00:10 Purpose - An AI skilling and venture building programme funded by the Japanese Ministry of Economy, Trade and Industry (METI). Its goal is to give innovators technical skills, entrepreneurial grounding and support to turn AI skills into ventures. Builds pipeline for timbuktoo Hubs (UNDP initiative offering early-stage capital, policy and technical support through ten thematic pan-African hubs).
00:30 UniPods - Makerspaces located in public universities across Africa providing technical support from ideation through prototyping, testing and market access. Currently 23 UniPods across 21 countries, with five more expected by December 2026.
01:00 Track 1 MIT Foundational AI - Massachusetts Institute of Technology (MIT) covers introduction to AI, Python coding, data analytics, sustainable energy, transportation, medicine, entrepreneurship. Self-paced, covered via fee waiver code (normally $900). Enrolment via individual link sent to applicant email. Dashboard access required. Expected completion: 18 October 2026. 16 foundational modules are compulsory; vertical modules are optional. Contact: uaisupport@mit.edu.
01:30 Track 2 Wadhwani Ignite Entrepreneurship - 14-week experiential venture building curriculum by Wadhwani Foundation. Instructor-led by Charles Bolton. Tuesdays 3:00 PM CAT (live class), Thursdays 3:00 PM CAT (live coaching & Q&A). In-person UniPod meetups every three weeks. Module 1 Problem Statement: max 350 characters, customer-focused, root cause, who will pay. Venture creation on platform: one venture per team.
02:00 Track 3 Ethiopian AI Institute - Anchored in Ethiopian AI Institute in Addis Ababa. 3-month instructor-facilitated virtual programme covering intermediate to advanced AI. Builds directly on MIT coursework. Selected participants advance to in-person bootcamp.
02:30 How It Connects - Cohort 1 starts with 250 solutions. One person registered for MIT (completed within 1 month). Wadhwani runs concurrently (at least one member must complete). Teams on track proceed to Ethiopian AI Institute. In late November 2026, the 50 strongest solutions are selected for the in-person Addis Ababa bootcamp (opening Dec 1, 2026; 1 person per team). Solutions not selected considered for second bootcamp in Feb 2027.
03:00 Contacts - Official program email: unipods.regional@undp.org. Main program coordinator/admin: Diane (+250 783 188 655). Leadership: Gift Ntuli, Jeovaire Umukundwa, Munira."""

HACKATHON_CONTENT = """00:00 Hackathon Overview - UniPods Chatbot Hackathon for METI UniPods AI Innovation Programme Cohort 1. $5,000 cash prize for the winning team.
00:15 The Problem - Chat volume across cohort is very high; participants miss critical announcements and repeatedly ask already answered questions. Members miss live sessions and cannot watch all recordings.
00:30 The Challenge - Build a working community memory chatbot that ingests group discussions, announcements, and call recordings, and directly answers member queries with evidence.
00:45 Deliverables - A working chatbot (not just slides or prototype), public or repository access to source code, and concise setup/maintenance documentation. Platform can be WhatsApp, Telegram, or Web.
01:00 Team Rules - Maximum 5 members per team. Must include at least one woman. Members cannot all be from the same country; up to 2 members from the same country are allowed.
01:15 Timeline & Submission - Team declarations due Sept 17, 2026 by email to unipods.regional@undp.org (Subject: UniPods Hackathon – Team Declaration). Hackathon build period runs Sept 18 to Sept 24, 2026.
01:30 Testing & Deployment - Teams must notify Diane before deployment; bots are tested rotationally in the community (e.g., Shadrak's bot tested Sept 18-19, AskBack AI Bot scheduled Sept 28). Bot names must follow format: [TEAMNAME] BOT (e.g., SPARK BOT). Judging is conducted by a vote of the full cohort."""

COHORT_KNOWLEDGE = """00:00 Important Recordings - All official session recordings:
00:10 MIT Recording - MIT Universal AI Welcome & Onboarding (16 Sept 2026): https://drive.google.com/file/d/1E5RrwULX8zSjwxHFSxiQzCTtp20ulYQ8/view
00:20 Wadhwani Module 0 - Welcome & Module 0 (10 Sept 2026): https://youtu.be/yVji4ZQECVw
00:30 Wadhwani Module 1 - Class Session (15 Sept 2026): https://youtu.be/6q4uPBO_sDc
00:40 Wadhwani Coaching - Problem Statement Q&A (17 Sept 2026): https://youtu.be/-6G7LXiu47o
01:00 Open Hours - Weekly Open Hours: Mondays with Gift Ntuli at 3:00 PM CAT, Wednesdays with Diane at 3:00 PM CAT. Teams link: https://teams.microsoft.com/l/meetup-join/19%3ameeting_MjlkNWYyMjYtMGNhMi00NDM1LTlkNmYtOTZhYTU2MDU4MDc2%40thread.v2/0?context=%7B%22Tid%22%3A%22b3e5db5e-2944-4837-99f5-7488ace54319%22%2C%22Oid%22%3A%2225f213f2-0e2f-4763-83fa-0d909a0e9701%22%7D
01:20 UN Video Opportunity - 5-minute showcase video for UN General Assembly (Sept 20, 2026 in New York). Deadline was Friday Sept 18 at 2:00 PM CAT. Submission: Country_SolutionName_YourName to unipods.regional@undp.org.
01:40 Team AskBack & Other Teams - Teams formed during cohort: Team AskBack (Onalenna - Botswana, Liane - Madagascar, Hassanat - Nigeria, Isaac - Nigeria, Adolphe - Rwanda). Team JOTDS (Shema Owen - Rwanda, Joel - Rwanda, Deborah - Rwanda, Reitumetse - Lesotho, Kgosi - Botswana). Other teams: EcoSync, Swift Agents, Wise-Bot, PodPal BOT."""

# Real team chats
TEAM_CHATS = [
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T20:01:53+02:00", "text": "Hello @all once again every one is free to share their ideas on the task. Thank you"},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-16T20:02:36+02:00", "text": "Hi. Kgosi. Botswana. Business Development and Project Management"},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T20:20:37+02:00", "text": "My name is Shema Owen from Rwanda full-stack engineer, software developer. Joel and Deborah are still at work but anytime they will join."},
    {"author": "Deborah", "author_id": "deborah_rwanda", "time": "2026-09-16T21:01:32+02:00", "text": "Hello I am Deborah from Rwanda, software engineer, QA."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T21:36:55+02:00", "text": "What time are you available and we make a google meet @all"},
    {"author": "joe", "author_id": "+250789201681", "time": "2026-09-16T21:37:04+02:00", "text": "Hello everyone @all am Joel software engineer and architect from Rwanda"},
    {"author": "joe", "author_id": "+250789201681", "time": "2026-09-16T21:41:16+02:00", "text": "For me right now is good"},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T22:43:52+02:00", "text": "POLL: Time for our meeting. Option 1: 10:30pm CAT, Option 2: 11:00pm CAT"},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T22:56:31+02:00", "text": "Meeting link: https://meet.google.com/ibg-ehky-pfr hello you can join the meeting"},
    {"author": "Deborah", "author_id": "deborah_rwanda", "time": "2026-09-16T23:01:52+02:00", "text": "Joining Google Meet now..."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-16T23:23:37+02:00", "text": "Shared UniPods Hackathon Guidelines.pdf document with the team."},
    {"author": "Reitumetse (T&R Analytics)", "author_id": "reitumetse_lesotho", "time": "2026-09-17T00:23:19+02:00", "text": "Hi everyone. I’m Reitumetse from Lesotho, an Economist, AI & Business Solutions specialist, and Full-Stack Developer. Looking forward to working with you all. I apologise for missing yesterday's meeting."},
    {"author": "joe", "author_id": "+250789201681", "time": "2026-09-17T00:25:09+02:00", "text": "It's fine we understand. In the morning we will update you on what we discussed and the action plan on how we want to proceed."},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-17T08:17:30+02:00", "text": "Hello guys, my internet was cut last night. Apologies. We will be waiting for the update. Thanks!"},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-17T10:37:58+02:00", "text": "Good morning @all. 11:00 am CAT we have meeting if you are available you can join."},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-17T10:39:12+02:00", "text": "In a workshop right now. Send meeting notes. Don't forget to send team name and members to UniPod."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-17T13:20:27+02:00", "text": "Hello everyone please share your GitHub usernames."},
    {"author": "T&R Analytics", "author_id": "reitumetse_lesotho", "time": "2026-09-17T14:20:28+02:00", "text": "My GitHub username is: byte0107"},
    {"author": "T&R Analytics", "author_id": "reitumetse_lesotho", "time": "2026-09-17T14:30:35+02:00", "text": "Reitumetse Sehloho. I received the invitation."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-17T14:34:28+02:00", "text": "Yeah accept the invitation."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-17T14:18:35+02:00", "text": "Is there anyone who knows how we are going to submit team names?"},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-17T14:19:14+02:00", "text": "Check on the group there, they said by email: unipods.regional@undp.org."},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-18T18:26:21+02:00", "text": "Hello guys. We need to start developing the bot well in time. Share link or something of the work so far."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-18T18:47:18+02:00", "text": "We started developing yesterday, me and Joel. If you are around you can join the meeting: https://meet.google.com/jcj-vkiv-wxa"},
    {"author": "Kgosi", "author_id": "kgosi_botswana", "time": "2026-09-18T18:48:33+02:00", "text": "Around 21:00 I will be arriving. I will make contact with you."},
    {"author": "Shema Owen", "author_id": "+250782972679", "time": "2026-09-19T03:43:01+02:00", "text": "Don't mind the deleted messages, I was testing. You can check on GitHub there are updates!"}
]

# Curated high-signal announcements from the main cohort group
COHORT_MESSAGES = [
    {"author": "Diane (Coordinator)", "time": "2026-09-04T13:22:17+02:00", "text": "Welcome to the UniPods AI Programme group! Share your excitement on social media and tag METI, timbuktoo and UNDP."},
    {"author": "Gift Ntuli (Lead)", "time": "2026-09-05T19:56:09+02:00", "text": "Capital funding will be accessible at the end of the program for businesses in the cohort."},
    {"author": "Gift Ntuli (Lead)", "time": "2026-09-07T18:27:49+02:00", "text": "You continue with the solution you registered, but it is encouraged to merge solutions with other innovators to form new ventures."},
    {"author": "Charles Bolton (Wadhwani Coach)", "time": "2026-09-10T15:00:00+02:00", "text": "Welcome to Wadhwani Ignite! Live class sessions every Tuesday 3PM CAT, coaching & QA every Thursday 3PM CAT. Focus on problem statement without describing the solution."},
    {"author": "Diane (Coordinator)", "time": "2026-09-11T20:04:33+02:00", "text": "Send the name of your solution and team members (max 5 people, cannot be changed) to unipods.regional@undp.org."},
    {"author": "Munira (UNDP)", "time": "2026-09-14T16:07:38+02:00", "text": "MIT Universal AI session link: https://teams.microsoft.com/meet/369123389215172. The course is self-paced."},
    {"author": "Gift Ntuli (Lead)", "time": "2026-09-16T15:18:50+02:00", "text": "Proposal: If anyone creates an AI bot that reads previous messages and creates a response to new similar questions we will pay for it immediately ($5,000 prize)."},
    {"author": "Gift Ntuli (Lead)", "time": "2026-09-16T15:25:36+02:00", "text": "We have one 5-minute slot to present a pre-recorded demo at the UN General Assembly event 'Building the Workforce of the Future' in New York on 20 Sep. Submit by Friday 18 Sep 2PM CAT to unipods.regional@undp.org."},
    {"author": "Diane (Coordinator)", "time": "2026-09-16T18:25:08+02:00", "text": "Hackathon Guidelines shared: Build a working chatbot for FAQ & info retrieval. Prize $5,000. Team declarations due Sept 17 by email. Hackathon runs Sept 18-24. Up to 5 people, at least 1 woman, not all from same country."},
    {"author": "Gift Ntuli (Lead)", "time": "2026-09-16T18:41:24+02:00", "text": "Team composition poll result: Up to 2 members from the same country are allowed on a 5-person team."},
    {"author": "Diane (Coordinator)", "time": "2026-09-17T17:03:16+02:00", "text": "METI Cohort 1 Open Hours: Ask Us Anything every Monday with Gift Ntuli and Wednesday with Diane at 3:00 PM CAT."},
    {"author": "Diane (Coordinator)", "time": "2026-09-18T13:16:54+02:00", "text": "When creating your bot, name it using your team name followed by 'BOT' (e.g. SPARK BOT). Teams must inform Diane before deploying to group for rotational testing."},
    {"author": "Shadrak (Participant)", "time": "2026-09-18T14:13:37+02:00", "text": "Tested first bot (meti_bot). Tested on Sept 18-19. Other teams will be assigned testing slots by Diane (e.g. AskBack AI Bot on Sept 28)."},
    {"author": "Diane (Coordinator)", "time": "2026-09-19T19:36:32+02:00", "text": "Diane is the main METI program coordinator and admin (+250 783 188 655). She coordinates announcements, testing schedules, and forms."},
    {"author": "Participant Query & Answer", "time": "2026-09-19T20:30:00+02:00", "text": "All meeting recordings: MIT Onboarding (https://drive.google.com/file/d/1E5RrwULX8zSjwxHFSxiQzCTtp20ulYQ8/view), Wadhwani Module 0 (https://youtu.be/yVji4ZQECVw), Wadhwani Module 1 (https://youtu.be/6q4uPBO_sDc), Wadhwani Coaching (https://youtu.be/-6G7LXiu47o)."}
]

async def master_ingest():
    await init_db()
    async with SessionLocal() as db:
        print("[1/5] Clearing outdated data...")
        await db.execute(delete(Chunk))
        await db.execute(delete(Message))
        await db.execute(delete(Decision))
        await db.execute(delete(ActionItem))
        await db.execute(delete(Meeting))
        await db.commit()

        print("[2/5] Ingesting Official METI Information Pack...")
        m1 = await ingest_transcript_file(
            db,
            title="METI UniPods AI Innovation Programme Information Pack",
            content=INFO_PACK_CONTENT,
            filename="METI_Info_Pack.txt"
        )
        print(f"  -> Ingested Info Pack (Meeting id={m1.id})")

        print("[3/5] Ingesting Official Hackathon Guidelines & Rules...")
        m2 = await ingest_transcript_file(
            db,
            title="UniPods Hackathon Guidelines & Deliverables",
            content=HACKATHON_CONTENT,
            filename="Hackathon_Guidelines.txt"
        )
        print(f"  -> Ingested Hackathon Guidelines (Meeting id={m2.id})")

        print("[4/5] Ingesting Recordings, Open Hours & Community Knowledge...")
        m3 = await ingest_transcript_file(
            db,
            title="UniPods Cohort Master Knowledge & Links",
            content=COHORT_KNOWLEDGE,
            filename="Cohort_Knowledge.txt"
        )
        print(f"  -> Ingested Master Links & Schedule (Meeting id={m3.id})")

        print("[5/5] Ingesting Team Chats & Cohort Community History...")
        msgs = []
        for i, c in enumerate(TEAM_CHATS):
            msgs.append(NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=SourceType.CHAT,
                external_id=f"team-chat-{i+1}",
                conversation_id="UNIPOD TASK GROUP",
                author_id=c["author_id"],
                author_name=c["author"],
                text=c["text"],
                timestamp=datetime.fromisoformat(c["time"]).isoformat(),
                metadata={"is_group": True, "conversation_id": "UNIPOD TASK GROUP"}
            ))

        for i, c in enumerate(COHORT_MESSAGES):
            msgs.append(NormalizedMessage(
                platform=Platform.WHATSAPP,
                source_type=SourceType.CHAT,
                external_id=f"cohort-chat-{i+1}",
                conversation_id="UNIPODS COHORT 1",
                author_id="cohort_member",
                author_name=c["author"],
                text=c["text"],
                timestamp=datetime.fromisoformat(c["time"]).isoformat(),
                metadata={"is_group": True, "conversation_id": "UNIPODS COHORT 1"}
            ))

        stored = await ingest_messages(db, msgs)
        print(f"  -> Ingested {len(stored)} messages across team and cohort!")

        # Add structured Decisions
        db.add(Decision(
            decision="Team Name: JOTDS",
            reason="Formed for the METI UniPods AI Innovation Programme Hackathon",
            decided_at=datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc),
            context="UniPods Hackathon Cohort 1",
            authors=["Shema Owen", "Joel", "Deborah", "Reitumetse", "Kgosi"]
        ))
        db.add(Decision(
            decision="GitHub for Code Collaboration",
            reason="Team code repository for chatbot implementation and PR reviews",
            decided_at=datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc),
            context="Chatbot development setup",
            authors=["Shema Owen", "Joel"]
        ))
        db.add(Decision(
            decision="Hackathon Prize: $5,000 for the single winning team decided by cohort vote",
            reason="Confirmed by Gift Ntuli and Hackathon Guidelines",
            decided_at=datetime(2026, 9, 16, 18, 30, tzinfo=timezone.utc),
            context="Hackathon incentive and judging",
            authors=["Gift Ntuli", "Diane"]
        ))

        # Add Action Items
        db.add(ActionItem(
            task="Build and test the WhatsApp Community Memory Chatbot with PostgreSQL RAG",
            assignee_name="Shema Owen & Joel",
            due_at=datetime(2026, 9, 24, 23, 59, tzinfo=timezone.utc)
        ))
        db.add(ActionItem(
            task="Complete MIT Universal AI foundational modules (16 modules) by October 18, 2026",
            assignee_name="Team Lead / Registered Member",
            due_at=datetime(2026, 10, 18, 23, 59, tzinfo=timezone.utc)
        ))
        db.add(ActionItem(
            task="Submit Wadhwani Module 1 Problem Statement (max 350 chars) before Tuesday class",
            assignee_name="All Venture Founders",
            due_at=datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
        ))
        await db.commit()
        print("  -> Structured decisions and action items saved.")

        print("\n=== Comprehensive Ingestion Finished Successfully! ===")

if __name__ == "__main__":
    asyncio.run(master_ingest())
