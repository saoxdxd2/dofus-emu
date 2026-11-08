# DofusFarm — Upgrade Review: Flaws, Hardcodes & Required Changes

**Scope:** byte-level review of every source layer (C# host `desktop/`, Go relay `scripts/dofus-net-proxy.go`, Python brain `dofus-network/`, JS client adapter `scripts/mobile-disguise.js` + `client/www/js/*`), plus the config files (`network.ini`, `ai_settings.ini`) and the project's own defect register (`dofus-network/docs/KNOWN_GAPS.md`, G1–G109).

**Goal:** the smartest game-playing AI on the cheapest budget — every change below is weighed against the shipped constraints (~200 MB RAM/instance, <10 ms planning step, fail-closed egress, no torch in the runtime, offline-first).

---

## Severity key

| Severity | Meaning |
|---|---|
| **BLOCKER** | Breaks or silently corrupts on a client/server update; must be fixed before any upgrade. |
| **HIGH** | Causes misbehavior, wasted steps, or leaked identity under normal operation. |
| **MEDIUM** | Hardcoded value that works today but must become configurable before scaling. |
| **LOW** | Hygiene / technical debt that slows the next change. |

## Open Hardcoded-part inventory (file:line → value → why it must change)

*(All 21 initial hardcoded items resolved and verified)*

### Verification Status Across All Layers
- **Hardcode Guard Tests:** 31 / 31 test suites passing in `tools/test_no_hardcoded_*.py`
  - `test_no_hardcoded_advisor_constants.py`: PASS
  - `test_no_hardcoded_agent_constants.py`: PASS
  - `test_no_hardcoded_android_auth.py`: PASS
  - `test_no_hardcoded_cdp_port.py`: PASS
  - `test_no_hardcoded_chrome_ua.py`: PASS
  - `test_no_hardcoded_combat_ports.py`: PASS
  - `test_no_hardcoded_cs_constants.py`: PASS
  - `test_no_hardcoded_cs_ports.py`: PASS
  - `test_no_hardcoded_device_ids.py`: PASS
  - `test_no_hardcoded_dialog_policy.py`: PASS
  - `test_no_hardcoded_egress_defaults.py`: PASS
  - `test_no_hardcoded_farmengine_constants.py`: PASS
  - `test_no_hardcoded_feature_scaling.py`: PASS
  - `test_no_hardcoded_foresight_constants.py`: PASS
  - `test_no_hardcoded_gate_constants.py`: PASS
  - `test_no_hardcoded_google_urls.py`: PASS
  - `test_no_hardcoded_installer_ports.py`: PASS
  - `test_no_hardcoded_knowledge_store.py`: PASS
  - `test_no_hardcoded_memory_limit.py`: PASS
  - `test_no_hardcoded_model_paths.py`: PASS
  - `test_no_hardcoded_netsettings.py`: PASS
  - `test_no_hardcoded_network_targets.py`: PASS
  - `test_no_hardcoded_page_origin.py`: PASS
  - `test_no_hardcoded_plan_stack.py`: PASS
  - `test_no_hardcoded_pool_ua.py`: PASS
  - `test_no_hardcoded_proxy_urls.py`: PASS
  - `test_no_hardcoded_sdk_paths.py`: PASS
  - `test_no_hardcoded_state_features.py`: PASS
  - `test_no_hardcoded_verifier_thresholds.py`: PASS
  - `test_no_hardcoded_webview_urls.py`: PASS
  - `test_no_hardcoded_world_state.py`: PASS
- **Live Gameplay & Dialogue Fixes:**
  - `tools/test_tutorial_dialog_reply.py`: 8 / 8 tests PASS
  - `tools/test_char_build.js`: 14 / 14 tests PASS
  - `tools/test_npc_actor.js`: 8 / 8 tests PASS
  - `dofus-network/tools/test_dialogue_fixes.py`: 3 / 3 tests PASS
- **Agent Unit Tests:** 21 / 21 passing in `dofus-network/agent/`
- **Tool Unit Tests:** 197 / 197 passing in `dofus-network/tools/`
- **Kaggle Toolkit Parity:** Synchronized via `sync_kaggle_toolkit.py` (0 drift / 0 missing across 246 files)
- **Compiler Release Suite:** Standalone compilation (`cmd /c compile.bat`) verified 100% green: `dofus-net-proxy.exe`, `DofusFarm.exe`, `uninstall.exe`, and standalone `setup.exe` (59.1 MB) generated with zero build errors.

## Open known gaps from `KNOWN_GAPS.md` that still block a real upgrade

The project's own register (G1–G109) is the authoritative defect list. Items **not** marked fixed/closed and still upgrade-relevant:

| Gap | Summary | Status signal | Upgrade impact |
|---|---|---|---|
| G4 | No vision / image encoder | open | Cannot read map icons, HUD, or dialog text visually; planner depends entirely on wire/observation JSON. |
| G5 | Network-health forecaster dark | open (W4 carried from `PRETRAIN_GAPS.md`) | RTT/loss forecasting absent; egress choice is reactive, not predictive. |
| G6 | Verifier head wired by GUI, unmeasured | GUI auto-load gated | Learned verifier not proven live; rule baseline still doing all the labeling. |
| G7 | Live combat unverified against real server | offline-tested only | Combat solver/ranker may not match live server damage/turn resolution. |
| G9 | Identity keyed by slot, not account | fail-closed shipped; live relogin proof owed | Account ban-risk; a slot recycle rebinds to a new guest identity unexpectedly. |

## Architecture-level upgrade observations

- **Strength:** The agent loop is rule-first with a separate controller/verifier, so it can improve without a dataset (labels fall out of the loop). This is the correct "cheapest budget" starting point.
- **Strength:** Features are single-sourced from `data/capabilities.json` (28 tools) so the action space cannot drift between schema and features (guarded by `agent/regression.py`).
- **Risk:** The 49K-tool policy head still has 21 trained logits while 7 newest tools are rule-only (`rules.md`); the learned head and legal registry are separate contracts, so a trained policy will silently refuse the 7 new tools until retrained.
- **Risk:** `networkhealth_artifacts/` is absent on this machine; the forecaster (G5) cannot be validated locally.

