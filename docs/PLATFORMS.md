# WhatsApp via Wassenger

UniPods Memory uses **Wassenger** as the WhatsApp provider.

## Configured device

- Phone: `+250727516637` (Unipod Ai JOTDS)
- Device ID: `6aad797372135ef67226b310`
- Env: `WASSENGER_API_KEY`, `WASSENGER_DEVICE_ID`, `WASSENGER_PHONE`

## Group bot behavior

1. **Every group message** is saved into community memory  
2. If a message **needs clarification** (question / “what was decided” / “when is…” / etc.), the bot searches **prior shared chats**  
3. It replies **in the group** with the answer + evidence (only when evidence exists — no spam on chit-chat)  
4. Mock/demo seed data is removed — use real WhatsApp traffic only

1. Start the backend on port 8000
2. Expose it:
   ```powershell
   ngrok http 8000
   ```
3. In [Wassenger console](https://app.wassenger.com/) → **Webhooks** → create:
   - **URL:** `https://YOUR-NGROK/api/v1/webhooks/whatsapp`
   - **Event:** `message:in:new`
4. Message the WhatsApp number and ask:
   ```
   What did they decide about the database?
   /catchup
   ```

## Note on billing

Wassenger reported a Meta payment issue on this number (`payment_issue`).  
Outbound sends may fail until the Meta Business payment method is fixed.  
Inbound webhooks + local simulate still work for the demo.

## API paths

| Path | Purpose |
|------|---------|
| `POST /api/v1/webhooks/whatsapp` | Wassenger inbound events |
| `GET /api/v1/platforms/status` | Connection status |
| `POST /api/v1/platforms/simulate` | Local test without WhatsApp |
