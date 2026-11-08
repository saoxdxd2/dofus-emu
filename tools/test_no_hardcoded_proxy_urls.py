"""Guard against hardcoded proxy source URLs in `dofus-network/ai/pool_runner.py`.

`https://api.proxyscrape.com/v2/?...` URLs are baked into source. If the proxy
source changes or goes down, pool refresh breaks. These should come from config.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_proxy_urls.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL_RUNNER = REPO / "dofus-network" / "ai" / "pool_runner.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = POOL_RUNNER.read_text(encoding="utf-8", errors="replace")

if "api.proxyscrape.com" in text:
    fail("pool_runner.py hardcodes proxyscrape.com API URLs — make configurable")
else:
    ok("proxyscrape.com URLs not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded proxy source URL(s) still present")
    sys.exit(1)
print("no hardcoded proxy source URLs detected")
sys.exit(0)