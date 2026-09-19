import time
import httpx

for i, plat in enumerate(["whatsapp", "teams", "whatsapp"]):
    t = time.time()
    r = httpx.post(
        "http://localhost:8000/api/v1/platforms/simulate",
        json={
            "platform": plat,
            "text": "What did they decide about the database?",
        },
        timeout=30,
    )
    d = r.json()
    answer = (d.get("data") or {}).get("answer", "")[:90]
    print(f"{i+1}. {plat}: {r.status_code} {time.time()-t:.2f}s | {answer}")
