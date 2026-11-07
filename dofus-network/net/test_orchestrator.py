"""Arms for the network pipeline: ownership, not liveness, is what these assert.

The bug this guards is a healthy answer from the wrong process. A test that only checks
"probe says ok" would have passed straight through the 2026-11-04 incident, where a brain started
50 minutes and three app restarts earlier answered :8891 on the dev interpreter. So every arm here
either proves a row is *this* run's or proves the pipeline refuses to act on something it cannot
identify -- and the negative arms are asserted too, because a classifier that always says
"this-run" is the bug wearing a badge.

    python -m net.test_orchestrator          (from dofus-network/)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from net import orchestrator as oc
from combat import brain_service as bs

ARMS = []


def arm(name, got, want):
    ok = got == want
    ARMS.append({"arm": name, "pass": ok, "got": str(got)[:120], "want": str(want)[:120]})
    return ok


def fake_probe(**fields):
    def probe(port, timeout=1.5):
        base = {"answering": True, "port_open": True}
        base.update(fields)
        return base
    return probe


def component(name="brain", owned=True, port=18891, probe=None):
    return {"name": name, "port": port, "probe": probe or fake_probe(), "owned": owned,
            "expect": "combat_ai_server.py", "depends": [], "note": "test"}


def classify_row(comp, token, pid=4242, image="python.exe"):
    real_owner, real_name = oc.owner_pid, oc.process_name
    oc.owner_pid = lambda p: pid
    oc.process_name = lambda p: image
    try:
        return oc.classify(comp, token)
    finally:
        oc.owner_pid, oc.process_name = real_owner, real_name


def main() -> int:
    # --- ownership states ---------------------------------------------------------------
    row = classify_row(component(probe=fake_probe(identity={"token": "abc123"})), "abc123")
    arm("matching token is this-run", row.get("run"), "this-run")

    row = classify_row(component(probe=fake_probe(identity={"token": "zzz999"})), "abc123")
    arm("other token is foreign", row.get("run"), "foreign")

    row = classify_row(component(probe=fake_probe(identity={})), "abc123")
    # Sharpened, not weakened: a live scan showed the orphan brain on :8891 answering healthily
    # with no identity field at all, and "cannot tell" was being read as reusable. It is foreign.
    arm("an owned server with no identity is foreign", row.get("run"), "foreign-unlabelled")

    row = classify_row(component("relay", owned=False, probe=fake_probe()), "abc123",
                       image="dofus-net-proxy.exe")
    arm("a host-owned server is not judged by identity", row.get("run"), None)

    row = classify_row(component(probe=fake_probe(identity={"token": ""})), "abc123")
    arm("unlabelled server is foreign-unlabelled", row.get("run"), "foreign-unlabelled")

    row = classify_row(component(probe=fake_probe(answering=False)), "abc123")
    arm("bound but silent", row.get("state"), "bound-not-answering")

    row = classify_row(component(probe=fake_probe(answering=False, port_open=False)), "abc123")
    arm("nothing there", (row.get("state"), row.get("owner_pid")), ("down", 4242))

    # --- plan: act only on what this pipeline owns ---------------------------------------
    comp_owned, comp_host = component("brain"), component("relay", owned=False, port=18880)
    saved = oc.COMPONENTS
    oc.COMPONENTS = [comp_host, comp_owned]
    try:
        real_owner, real_name = oc.owner_pid, oc.process_name
        oc.owner_pid = lambda p: 1
        oc.process_name = lambda p: "python.exe"
        try:
            actions = {a["component"]: a["action"] for a in oc.plan("abc123")}
        finally:
            oc.owner_pid, oc.process_name = real_owner, real_name
        arm("foreign owned brain is reaped and restarted", actions["brain"], "reap-then-start")
        arm("host-owned relay is never touched", actions["relay"], "report-only")
    finally:
        oc.COMPONENTS = saved

    # --- ensure must reach a verified this-run, and must be able to say it did not -------
    calls = []

    def actor(comp, token):
        calls.append(comp["name"])
        return "started", {"pid": 5}

    def reporter(comp, token):
        return "report-only", {}

    good = component("brain", probe=fake_probe(identity={"token": "abc123"}))
    bad = component("brain", probe=fake_probe(identity={"token": "older"}))
    oc.COMPONENTS = [good]
    try:
        saved_classify = oc.classify
        result = oc.ensure("abc123", sleep=lambda s: None, actor=actor, timeout_s=0.5)
        arm("ensure acted once on the owned component", calls, ["brain"])
        arm("ensure reports a clean pipeline", result["clean"], True)
    finally:
        oc.COMPONENTS = saved

    calls = []
    oc.COMPONENTS = [bad]
    try:
        result = oc.ensure("abc123", sleep=lambda s: None, actor=actor, timeout_s=0.2)
        arm("ensure is not fooled by a foreign answer", result["clean"], False)
        arm("the foreign component is named as a problem",
            result["after"]["problems"], ["brain"])
    finally:
        oc.COMPONENTS = saved

    # --- the reaper's refusal is the safety property, not an error -----------------------
    body_mine = {"identity": {"pid": 9999, "script": str(bs.BRAIN_SCRIPT), "token": "old"}}
    body_theirs = {"identity": {"pid": 9999, "script": r"C:\somewhere\else.py", "token": "old"}}
    killed = []
    verdict = bs.reap_foreign_brain(body_theirs, killer=lambda pid: killed.append(pid))
    arm("refuses to kill an unprovable owner", verdict["reaped"], False)
    arm("and it did not even try", killed, [])
    verdict = bs.reap_foreign_brain(body_mine, killer=lambda pid: killed.append(pid))
    arm("kills a proven stale brain of ours", (verdict["reaped"], killed), (True, [9999]))
    os.environ["DOFUS_NO_REAP"] = "1"
    try:
        killed = []
        verdict = bs.reap_foreign_brain(body_mine, killer=lambda pid: killed.append(pid))
        arm("DOFUS_NO_REAP=1 opts the operator out", (verdict["reaped"], killed), (False, []))
    finally:
        os.environ.pop("DOFUS_NO_REAP", None)

    # The leftover actually found on :8891 predates the identity field, so health cannot prove it.
    # The OS command line can -- and an unrelated command line still must be refused.
    body_nameless = {"status": "ok", "identity": {"pid": 7696}}
    killed = []
    verdict = bs.reap_foreign_brain(body_nameless, killer=lambda p: killed.append(p), pid=7696,
                                    reader=lambda p: r"C:\...\python.exe C:\repo\dofus-network"
                                                     r"\combat\combat_ai_server.py 8891")
    arm("command line proves a nameless stale brain",
        (verdict["reaped"], verdict.get("proven_by"), killed), (True, "command-line", [7696]))
    killed = []
    verdict = bs.reap_foreign_brain(body_nameless, killer=lambda p: killed.append(p), pid=7696,
                                    reader=lambda p: r"C:\tools\someone-else\server.py 8891")
    arm("an unrelated command line is still refused", (verdict["reaped"], killed), (False, []))

    arm("same_run requires the token", bs.same_run_as_us(body_mine, "old"), True)
    arm("no token never matches", bs.same_run_as_us(body_mine, ""), False)
    arm("an identity-free answer never matches",
        bs.same_run_as_us({"status": "ok"}, "old"), False)

    # --- the token file itself: stale must not be trusted ---------------------------------
    tmp = Path(os.environ.get("TEMP", ".")) / "net_orch_token_test.json"
    tmp.write_text(json.dumps({"token": "stale", "created": time.time() - 99 * 3600}),
                   encoding="utf-8")
    fresh = oc.run_token(path=tmp, write=True)
    arm("a stale token is replaced, not inherited", fresh != "stale", True)
    again = oc.run_token(path=tmp, write=True)
    arm("a fresh token is reused", again, fresh)
    tmp.unlink(missing_ok=True)

    # --- two tokens, two names: provenance must not overwrite authorisation ------------
    # run_brain.py mints this token before importing agent.serve, and agent.bridge's
    # ensure_local_secret() reads DOFUS_RUN_TOKEN *before* the secret file. Exporting the
    # session token under that name therefore replaced the auth secret for the whole
    # process: the brain then presented a 16-hex bearer while the host's relay held the
    # 64-hex file secret, and every loopback call from the brain answered 401 while every
    # service still reported healthy.
    saved = {key: os.environ.get(key) for key in ("DOFUS_RUN_TOKEN", oc.SESSION_TOKEN_ENV)}
    tmp2 = Path(os.environ.get("TEMP", ".")) / "net_orch_session_token_test.json"
    try:
        os.environ.pop("DOFUS_RUN_TOKEN", None)
        os.environ.pop(oc.SESSION_TOKEN_ENV, None)
        tok = oc.run_token(path=tmp2, write=True)
        arm("the session token is exported under its own name",
            os.environ.get(oc.SESSION_TOKEN_ENV), tok)
        arm("and never as the loopback auth secret",
            os.environ.get("DOFUS_RUN_TOKEN"), None)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tmp2.unlink(missing_ok=True)

    # The contract that black-screened the client. A relay answering `Access-Control-Allow-Origin: *`
    # is discarded by Chromium on the game's credentialed map XHR -- status 0 in the page, no log
    # line on the server. Checked on every run so a wildcard cannot return with the next update;
    # skipped only when no relay is running, and the skip is named so it cannot hide.
    relay = next((c for c in oc.COMPONENTS if c["name"] == "relay"), None)
    if relay and oc._tcp_open(relay["port"]):
        contract = oc.cors_contract(port=relay["port"])
        arm("running relay satisfies the credentialed CORS contract",
            (contract.get("ok"), contract.get("problems")), (True, []))
    else:
        arm("relay CORS contract SKIPPED (no relay listening)", "skipped", "skipped")

    # The default-actor path: every other arm injects its own actor, so the built-in one was never
    # executed and carried a NameError into the first real `ensure`. This arm is the cheap guard.
    saved = oc.COMPONENTS
    oc.COMPONENTS = [component("relay", owned=False, probe=fake_probe())]
    try:
        result = oc.ensure("abc123", sleep=lambda s: None, timeout_s=0.2)
        arm("default actor path constructs without raising",
            [a["action"] for a in result["actions"]], ["report-only"])
    except Exception as exc:
        arm("default actor path constructs without raising", "raised %s" % exc, "no exception")
    finally:
        oc.COMPONENTS = saved

    # --- one report shape, whatever wrote it -------------------------------------------------
    # The watchdog stores an `ensure` report (live view nested under `after`, no top-level
    # timestamp); a manual check stores a `status` report that has both. A reader that cannot tell
    # them apart either shows nothing or shows a three-hour-old green as if it were live -- and a
    # dead pipeline looking healthy on screen is the failure this whole module is a reply to.
    saved_status_path = oc.STATUS_PATH
    tmp_status = Path(os.environ.get("TEMP", ".")) / "net_status_shape_test.json"
    oc.STATUS_PATH = tmp_status
    try:
        nested = {"kind": "ensure", "token": "t", "clean": False, "problems": ["relay"],
                  "after": {"checked_at": 111.0,
                            "components": [{"component": "relay", "state": "down"}]}}
        tmp_status.write_text(json.dumps(nested), encoding="utf-8")
        got = oc.report_now()
        arm("a stored ensure report reads back with a component table",
            got.get("components"), nested["after"]["components"])
        arm("and with the timestamp a staleness test needs", got.get("checked_at"), 111.0)
        tmp_status.write_text(json.dumps({"checked_at": 222.0, "components": [], "problems": []}),
                              encoding="utf-8")
        arm("a status report is passed through as written",
            oc.report_now().get("checked_at"), 222.0)
        tmp_status.unlink(missing_ok=True)
        saved_probe = oc.status
        oc.status = lambda *a, **k: {"components": [], "problems": ["fresh-check"]}
        try:
            arm("with no stored report the route still answers, and says it checked now",
                oc.report_now().get("problems"), ["fresh-check"])
        finally:
            oc.status = saved_probe
    finally:
        oc.STATUS_PATH = saved_status_path
        tmp_status.unlink(missing_ok=True)

    failed = [a for a in ARMS if not a["pass"]]
    print(json.dumps({"arms": len(ARMS), "failed": len(failed),
                      "detail": ARMS if failed else "all pass",
                      "verdict": "PASS" if not failed else "FAIL"}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
