import datetime as dt
import sys
import time

import requests

session = requests.Session()
session.headers.update({
    "User-Agent": "NetworkHealthResearch/1.0",
    "Accept": "application/json",
})

now = int(dt.datetime.now(dt.timezone.utc).timestamp())
start = now - 7 * 86400

for mid in (1001, 1002):
    for attempt in range(2):
        t0 = time.time()
        try:
            r = session.get(
                f"https://atlas.ripe.net/api/v2/measurements/{mid}/results/",
                params={"start": start, "stop": now, "format": "json"},
                timeout=(5, 20),
            )
            payload = r.json()
            records = payload.get("results", payload) if isinstance(payload, dict) else payload
            print(
                f"mid {mid} attempt {attempt}: {r.status_code}, {len(records)} records, "
                f"{time.time() - t0:.1f}s, {len(r.content) / 1024:.0f} KB",
                flush=True,
            )
            break
        except Exception as exc:
            print(
                f"mid {mid} attempt {attempt}: {type(exc).__name__} after {time.time() - t0:.1f}s",
                flush=True,
            )
        sys.stdout.flush()
    time.sleep(0.5)
