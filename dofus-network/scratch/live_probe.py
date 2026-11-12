"""Call any page tool on a live client, without the executor's registry in the way.

`ToolExecutor` only dispatches the tools the agent is allowed to choose, which is the
right rule for the agent and the wrong one for a probe: `debug.map`, `debug.reload` and
the rest of the `debug.*` family are how a fix gets checked against the running client.

    python scratch/live_probe.py dofus-01 debug.map
    python scratch/live_probe.py dofus-01 state.snapshot --json
    python scratch/live_probe.py dofus-01 quest.advance quest_id=4278 npc_id=3912

Arguments are `key=value`, with the value read as JSON when it parses and as a string
otherwise, so `cell=269` is an int and `direction=left` is a word.
"""
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge import directive_url, local_headers, local_opener  # noqa: E402


def call(tool, args, instance, timeout=25):
    body = json.dumps({"id": uuid.uuid4().hex, "ts": round(time.time(), 3),
                       "directive": "tool", "tool": tool, "args": args,
                       "source": "probe", "target_instance": instance}).encode()
    headers = dict(local_headers())
    headers["Content-Type"] = "application/json"
    request = urllib.request.Request(directive_url(), data=body, headers=headers,
                                     method="POST")
    with local_opener().open(request, timeout=timeout) as response:
        return json.loads(response.read())


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    instance, tool = argv[0], argv[1]
    args = {}
    for token in argv[2:]:
        if token == "--json":
            continue
        key, _, raw = token.partition("=")
        try:
            args[key] = json.loads(raw)
        except ValueError:
            args[key] = raw
    out = call(tool, args, instance)
    payload = out.get("result") if isinstance(out.get("result"), dict) else out
    print(json.dumps(payload, indent=1 if "--json" in argv else None)[:6000])
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
