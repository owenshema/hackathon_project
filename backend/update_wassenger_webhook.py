"""Update an existing Wassenger webhook URL."""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv


async def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python update_wassenger_webhook.py <webhook_id> <webhook_url>")
        raise SystemExit(2)

    load_dotenv()
    api_key = os.getenv("WASSENGER_API_KEY")
    if not api_key:
        print("[Error] WASSENGER_API_KEY not found")
        raise SystemExit(1)

    webhook_id = sys.argv[1]
    webhook_url = sys.argv[2]
    headers = {"Token": api_key, "Content-Type": "application/json"}
    payload = {
        "name": "UniPods WhatsApp Bot",
        "url": webhook_url,
        "events": ["message:in:new"],
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        for method in ("patch", "put"):
            response = await getattr(client, method)(
                f"https://api.wassenger.com/v1/webhooks/{webhook_id}",
                headers=headers,
                json=payload,
            )
            print(f"[{method.upper()}] {response.status_code}: {response.text[:500]}")
            if response.status_code in (200, 201):
                return

    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
