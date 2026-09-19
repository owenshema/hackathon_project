# Product summary (from UniPods Memory AI docs + Evidence Replay)

## Positioning

Not another generic chatbot — a **Community Memory Engine**:

> Remember everything the community discussed so humans don't have to.

## Pain

- Decisions buried in long calls and voice notes
- Context scattered across WhatsApp, Teams, documents
- Newcomers / absentees re-ask answered questions

## Core features

1. **Catch Me Up** — personalized digest (important / decisions / discussions / tasks)
2. **Ask the Past** — cited Q&A; refuse when evidence is weak
3. **Decision Tracker** — same extraction pass as catch-up
4. **Who Said That?** — attribution search
5. **Ask the Meeting** — speaker + timestamp (e.g. 42:18)
6. **Evidence Replay** — jump to WhatsApp message, voice note, or meeting offset
7. **Cross-source trails** — WhatsApp → meeting → WhatsApp confirmation linked in one answer

## Demo script

1. Seed cross-source evidence (or use real UniPods group traffic)
2. Ask: *What did they decide about the database?*
3. Show: Decision + speaker + meeting timestamp + ▶️ Replay
4. Ask: *Why did we choose option B?* → multi-source evidence stack
5. Tap **Catch Me Up** for a missed-day recap

## Important constraint

Do **not** auto-record every Teams call for MVP. Manual upload only; Graph auto-ingest is stretch and requires tenant admin consent / transcript policies.
