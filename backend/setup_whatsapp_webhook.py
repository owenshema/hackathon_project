"""
Helper utility to manage Wassenger WhatsApp webhooks.
Usage:
    python setup_whatsapp_webhook.py list
    python setup_whatsapp_webhook.py set https://your-domain.ngrok-free.app
    python setup_whatsapp_webhook.py delete <webhook_id>
"""

import sys
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("WASSENGER_API_KEY")
DEVICE_ID = os.getenv("WASSENGER_DEVICE_ID")
WASSENGER_API = "https://api.wassenger.com/v1"

if not API_KEY:
    print("[Error] WASSENGER_API_KEY not found in environment/.env")
    sys.exit(1)

HEADERS = {
    "Token": API_KEY,
    "Content-Type": "application/json",
}


async def list_webhooks():
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(f"{WASSENGER_API}/webhooks", headers=HEADERS)
            if r.status_code != 200:
                print(f"[Error] Failed to fetch webhooks ({r.status_code}): {r.text}")
                return
            webhooks = r.json()
            print(f"\n--- Registered Wassenger Webhooks ({len(webhooks)}) ---")
            if not webhooks:
                print("No webhooks registered yet.")
            for h in webhooks:
                print(f"ID: {h.get('id')}")
                print(f"URL: {h.get('url')}")
                print(f"Events: {h.get('events')}")
                print(f"Status: {h.get('status')}")
                print("-" * 40)
        except Exception as exc:
            print(f"[Error] Network connection error: {exc}")


async def set_webhook(base_url: str):
    base_url = base_url.rstrip("/")
    if not base_url.startswith("http"):
        print("[Error] URL must start with http:// or https://")
        return
    if not base_url.endswith("/api/v1/webhooks/whatsapp"):
        webhook_url = f"{base_url}/api/v1/webhooks/whatsapp"
    else:
        webhook_url = base_url

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            # First check existing webhooks to avoid duplicates
            r = await client.get(f"{WASSENGER_API}/webhooks", headers=HEADERS)
            if r.status_code == 200:
                for h in r.json():
                    if h.get("url") == webhook_url:
                        print(f"[Info] Webhook already registered with ID: {h.get('id')}")
                        return

            payload = {
                "name": "UniPods WhatsApp Bot",
                "url": webhook_url,
                "events": ["message:in:new", "group:update"],
            }
            if DEVICE_ID:
                payload["device"] = DEVICE_ID

            r = await client.post(f"{WASSENGER_API}/webhooks", headers=HEADERS, json=payload)
            if r.status_code in (200, 201):
                data = r.json()
                print(f"[Success] Webhook successfully created!")
                print(f"ID: {data.get('id')}")
                print(f"URL: {webhook_url}")
                print(f"Events: {data.get('events')}")
            else:
                print(f"[Error] Failed to create webhook ({r.status_code}): {r.text}")
        except Exception as exc:
            print(f"[Error] Network connection error: {exc}")


async def delete_webhook(webhook_id: str):
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.delete(f"{WASSENGER_API}/webhooks/{webhook_id}", headers=HEADERS)
            if r.status_code in (200, 204):
                print(f"[Success] Deleted webhook {webhook_id}")
            else:
                print(f"[Error] Failed to delete webhook ({r.status_code}): {r.text}")
        except Exception as exc:
            print(f"[Error] Network connection error: {exc}")


import asyncio

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python setup_whatsapp_webhook.py list")
        print("  python setup_whatsapp_webhook.py set <url>")
        print("  python setup_whatsapp_webhook.py delete <webhook_id>")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "list":
        asyncio.run(list_webhooks())
    elif cmd == "set" and len(sys.argv) >= 3:
        asyncio.run(set_webhook(sys.argv[2]))
    elif cmd == "delete" and len(sys.argv) >= 3:
        asyncio.run(delete_webhook(sys.argv[2]))
    else:
        print("Unknown command or missing arguments.")
