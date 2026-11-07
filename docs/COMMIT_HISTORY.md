# COMMIT HISTORY — the record of what each commit changed

> **None of the commits below exist any more.** On 2026-11-07 at 13:36 the repository was
> collapsed to a single commit, *"DofusFarm — consolidated snapshot, 2026-11-07"*. Every sha in
> this file refers to a commit that has been deleted from the repository and from GitHub.
> `git show <sha>` fails for all of them. This document is the only surviving account of them —
> it is a record, not an index.

Generated 2026-11-07 from the repository itself, not from memory. Every entry was read
from `git log --numstat`, so the file counts and line deltas are exact.

**Why this file exists.** The history was rewritten twice on 2026-11-07: 109 commits carrying the
message `.` were squashed into their neighbours, and a further set of generic messages
(`done`, `last version`, `Update farmengine.cs`) went the same way. Squashing destroys the
individual commit, so this file records what each one changed *before* it disappeared.

**Where the removed commits live now: nowhere.** Both backup bundles were deleted on 2026-11-07
at the user's request, permanently — they did not go to the Recycle Bin. The original 295-commit
history therefore exists in no location: not on GitHub (force-pushed away), not locally, not on
disk. This file is the only surviving record of what those commits contained.

`git show <sha>` for any sha in Parts 1 and 3 fails. The *content* is not lost — every squashed
commit's tree was folded into the commit that survived it, and the current tree is byte-identical
to the pre-rewrite tree (`3060425…`). What is gone is the per-commit boundary and the ability to
diff against it.

Line counts are `insertions − deletions` against the commit's first parent. Binary files report
no line delta. Paths are shown by basename; the full list is in `git show --stat <sha>`.

---

## Part 1 — Removed: 109 commits squashed away

These carried no usable message. Each was folded into the next commit that did, so its
changes survive in the tree — only the commit boundary is gone. Listed oldest first.

- **2026-10-14** `167c2c6` · 237 file(s) · +135929 −0 · `sdk/emulator` — `gitignore`, `.gitignore`, `compile.bat`, +234 more
  > .
- **2026-10-14** `80e63a2` · 17 file(s) · +3718 −5497 · `desktop` — `gitignore`, `.gitignore`, `compile.bat`, +14 more
  > .
- **2026-10-14** `06f13ad` · 3 file(s) · +660 −353 · `desktop` — `mainform.cs`, `installer.cs`, `app.integrity`
  > .
- **2026-10-14** `a15ed1a` · 7 file(s) · +461 −142 · `desktop` — `program.cs`, `hardware.cs`, `mainform.cs`, +4 more
  > .
- **2026-10-14** `d04dea0` · 4 file(s) · +147 −713 · `desktop` — `build.cs`, `installer.cs`, `farmengine.cs`, +1 more
  > .
- **2026-10-14** `25eef76` · 6 file(s) · +360 −467 · `desktop` — `build.cs`, `installer.cs`, `farmengine.cs`, +3 more
  > .
- **2026-10-14** `9f24cea` · 1 file(s) · +952 −1867 · `scripts` — `mobile-disguise.js`
  > .
- **2026-10-14** `1330046` · 3 file(s) · +1185 −571 · `scripts` — `farmengine.cs`, `dofus-net-proxy.go`, `mobile-disguise.js`
  > .
- **2026-10-14** `96c9dce` · 1 file(s) · +5 −5 · `desktop` — `farmengine.cs`
  > .
- **2026-10-14** `de6415d` · 5 file(s) · +318 −114 · `desktop` — `build.cs`, `win32.cs`, `mainform.cs`, +2 more
  > .
- **2026-10-14** `c3ded73` · 5 file(s) · +667 −242 · `desktop` — `build.cs`, `mainform.cs`, `installer.cs`, +2 more
  > .
- **2026-10-14** `c8fbee7` · 1 file(s) · +0 −8 · `sdk/system-images` — `advancedFeatures.ini`
  > .
- **2026-10-14** `151be88` · 2 file(s) · +179 −110 · `desktop` — `mainform.cs`, `farmengine.cs`
  > .
- **2026-10-14** `661742e` · 149 file(s) · +697 −112992 · `sdk/emulator` — `win32.cs`, `mainform.cs`, `installer.cs`, +146 more
  > .
- **2026-10-14** `dd89639` · 4 file(s) · +48 −149 · `desktop` — `win32.cs`, `installer.cs`, `farmengine.cs`, +1 more
  > .
- **2026-10-14** `0bdfed3` · 2 file(s) · +170 −27 · `builder` — `build.cs`, `farmengine.cs`
  > .
- **2026-10-15** `4ba7b40` · 2 file(s) · +41 −35 · `builder` — `build.cs`, `farmengine.cs`
  > 0
- **2026-10-15** `5bdd8ae` · 2 file(s) · +1 −21 · `desktop` — `win32.cs`, `farmengine.cs`
  > .
- **2026-10-15** `08236b2` · 24 file(s) · +159 −0 · `{sdk => sdk-stripped-backup}/emulator` — `farmengine.cs`, `strip-sdk.ps1`, `UsbAssist.inf`, +21 more
  > .
- **2026-10-15** `4899753` · 27 file(s) · +1766 −3789 · `sdk-stripped-backup/emulator` — `build.cs`, `mainform.cs`, `farmengine.cs`, +24 more
  > .
- **2026-10-15** `a1c7359` · 1 file(s) · +302 −456 · `scripts` — `dofus-net-proxy.go`
  > .
- **2026-10-16** `5f959aa` · 4 file(s) · +99 −23 · `desktop` — `mainform.cs`, `farmengine.cs`, `strip-sdk.ps1`, +1 more
  > .
- **2026-10-16** `416a836` · 5 file(s) · +114 −72 · `desktop` — `build.cs`, `win32.cs`, `hardware.cs`, +2 more
  > .
- **2026-10-16** `acc4fce` · 6 file(s) · +72 −35 · `desktop` — `win32.cs`, `mainform.cs`, `watchdog.cs`, +3 more
  > .
- **2026-10-16** `77d2f68` · 3 file(s) · +72 −17 · `desktop` — `win32.cs`, `farmengine.cs`, `prioritygovernor.cs`
  > .
- **2026-10-16** `11f5af7` · 4 file(s) · +75 −30 · `desktop` — `mainform.cs`, `watchdog.cs`, `farmengine.cs`, +1 more
  > .
- **2026-10-16** `85d0b3a` · 4 file(s) · +172 −7 · `desktop` — `win32.cs`, `mainform.cs`, `farmengine.cs`, +1 more
  > .
- **2026-10-16** `1b8dfb7` · 3 file(s) · +170 −26 · `desktop` — `mainform.cs`, `farmengine.cs`, `prioritygovernor.cs`
  > .
- **2026-10-16** `bd10465` · 5 file(s) · +21 −19 · `builder` — `build.cs`, `farmengine.cs`, `advancedFeatures.ini`, +2 more
  > .
- **2026-10-16** `9494631` · 5 file(s) · +17 −18 · `desktop` — `farmengine.cs`, `mobile-disguise.js`, `advancedFeatures.ini`, +2 more
  > .
- **2026-10-16** `9c277cc` · 3 file(s) · +50 −37 · `desktop` — `farmengine.cs`, `mobile-disguise.js`, `advancedFeatures.ini`
  > .
- **2026-10-17** `22ec6c9` · 53 file(s) · +402 −2062 · `sdk-stripped-backup/emulator` — `test-smp.ps1`, `compare-cores.ps1`, `test-blackbox.ps1`, +50 more
  > .
- **2026-10-17** `5f96c03` · 177 file(s) · +29380 −9127 · `desktop/client` — `TASK_MEMORY.md`, `MASTER_INDEX.md`, `build.cs`, +174 more
  > .
- **2026-10-17** `e55f3de` · 5 file(s) · +289 −9 · `desktop/client` — `diag.ps1`, `live_test.ps1`, `WebView2ClientForm.cs`, +2 more
  > .
- **2026-10-17** `afc355f` · 11 file(s) · +754 −98 · `desktop` — `diag.ps1`, `win32.cs`, `mainform.cs`, +8 more
  > .
- **2026-10-17** `886f4f8` · 29 file(s) · +5783 −0 · `dofus-network/networkhealth` — `README.md`, `.gitignore`, `LAYOUT.md`, +26 more
  > .
- **2026-10-17** `f01036d` · 13 file(s) · +1911 −16 · `dofus-network/networkhealth` — `build.cs`, `mainform.cs`, `installer.cs`, +10 more
  > 0
- **2026-10-17** `18d76be` · 38 file(s) · +28810 −0 · `runtimes/python` — `mainform.cs`, `_bz2.pyd`, `_msi.pyd`, +35 more
  > .
- **2026-10-17** `c5e3041` · 2 file(s) · +86 −9 · `dofus-network/ai` — `test_dofus_native_ai.py`, `dofus_native_model.py`
  > .
- **2026-10-17** `c008828` · 16 file(s) · +1604 −42 · `desktop/client` — `.gitignore`, `build.cs`, `installer.cs`, +13 more
  > .
- **2026-10-17** `82e5117` · 2 file(s) · +190 −24 · `desktop` — `WebView2ClientForm.cs`, `script.js`
  > .
- **2026-10-17** `21e8c09` · 22 file(s) · +1100 −165 · `scripts` — `py_a.txt`, `py_b.txt`, `py_c.txt`, +19 more
  > .
- **2026-10-17** `18ba204` · 19 file(s) · +859 −1 · `tests` — `_apk_out.txt`, `_seg_iso.txt`, `_diff_out.txt`, +16 more
  > .
- **2026-10-17** `31c6ea0` · 13 file(s) · +0 −88 · `scripts` — `py_a.txt`, `py_b.txt`, `py_c.txt`, +10 more
  > .
- **2026-10-17** `675d10a` · 16 file(s) · +7883 −206288 · `dofus-network/ai` — `distill.py`, `__init__.py`, `pipeline.py`, +13 more
  > .
- **2026-10-17** `ea0f7e1` · 6 file(s) · +207901 −47383 · `dofus-network/data` — `pipeline.py`, `train_dofus_brain_t4.ipynb`, `acquire.py`, +3 more
  > .
- **2026-10-17** `d67a5ab` · 4 file(s) · +338670 −20 · `dofus-network/data` — `spells_full.json`, `acquisition_report.json`, `dofus_deep_training_corpus.json`, +1 more
  > .
- **2026-10-17** `2c3dcc5` · 11 file(s) · +558 −450 · `desktop` — `hardware.cs`, `mainform.cs`, `farmengine.cs`, +8 more
  > .
- **2026-10-17** `34d0a51` · 5 file(s) · +32 −753 · `desktop/client` — `mobile-disguise.js`, `WebView2ClientForm.cs`, `test_singleton_sentinel.py`, +2 more
  > .
- **2026-10-17** `d7fede7` · 3 file(s) · +198 −4 · `desktop` — `mobile-disguise.js`, `WebView2ClientForm.cs`, `mobile-disguise.js`
  > .
- **2026-10-17** `073b79d` · 12 file(s) · +635 −177 · `desktop/client` — `mainform.cs`, `patch-engine.js`, `js-error-finder.js`, +9 more
  > .
- **2026-10-18** `4e50a52` · 12 file(s) · +1176 −77 · `scripts` — `.go-version`, `go.mod`, `mainform.cs`, +9 more
  > .
- **2026-10-18** `f297762` · 6 file(s) · +6426 −6295 · `desktop` — `build.cs`, `program.cs`, `mainform.cs`, +3 more
  > .
- **2026-10-18** `4997869` · 5 file(s) · +3700 −1851 · `desktop` — `compile.bat`, `mainform.cs`, `WebView2ClientForm.cs`, +2 more
  > .
- **2026-10-18** `05442aa` · 7 file(s) · +2830 −1749 · `desktop` — `SECURITY.md`, `TASK_MEMORY.md`, `MASTER_INDEX.md`, +4 more
  > .
- **2026-10-18** `088fdf8` · 100 file(s) · +32353 −15 · `client/www` — `start-sentinel.bat`, `cordova.js`, `index.html`, +97 more
  > .
- **2026-10-18** `2739a89` · 2 file(s) · +29 −26 · `desktop` — `WebView2ClientForm.cs`, `episodic_skills.json`
  > .
- **2026-10-18** `bac26ea` · 25 file(s) · +1998 −30 · `dofus-network/tests` — `dofus-net-proxy.go`, `_diag.py`, `WebView2ClientForm.cs`, +22 more
  > .
- **2026-10-18** `a4f5f12` · 18 file(s) · +540 −275 · `dofus-network/tests` — `dofus-net-proxy.go`, `_diag.py`, `WebView2ClientForm.cs`, +15 more
  > .
- **2026-10-18** `8f2adb0` · 12 file(s) · +883 −23 · `desktop` — `.tmp_log.txt`, `.tmp_diff.txt`, `.tmp_root.txt`, +9 more
  > .
- **2026-10-18** `8b40069` · 18 file(s) · +3663 −3 · `dofus-network/pipeline` — `map-export.js`, `scratch_scan.js`, `dofus-net-proxy.go`, +15 more
  > .
- **2026-10-19** `c03019a` · 26 file(s) · +83946 −3196 · `scripts` — `proxies_ok.txt`, `proxies_raw.txt`, `build.cs`, +23 more
  > .
- **2026-10-19** `72fda12` · 21 file(s) · +1370 −1776 · `desktop` — `.tmp_log.txt`, `.tmp_diff.txt`, `.tmp_root.txt`, +18 more
  > .
- **2026-10-19** `92f3206` · 4 file(s) · +166 −9 · `desktop` — `build.cs`, `mainform.cs`, `farmengine.cs`, +1 more
  > .
- **2026-10-20** `e98ebf2` · 38 file(s) · +8759 −10 · `dofus-network/agent` — `.gitignore`, `build.cs`, `mainform.cs`, +35 more
  > .
- **2026-10-20** `6acfbc6` · 10 file(s) · +2244 −46 · `dofus-network/ai` — `thx_policy.py`, `selftest.py`, `thx_finetune.py`, +7 more
  > .
- **2026-10-20** `be71196` · 10 file(s) · +46 −2244 · `dofus-network/ai` — `thx_policy.py`, `selftest.py`, `thx_finetune.py`, +7 more
  > .
- **2026-10-21** `3038399` · 122 file(s) · +116950 −63 · `scripts` — `valid`, `js_out.txt`, `read_js.py`, +119 more
  > .
- **2026-10-21** `1976e33` · 30 file(s) · +29543 −25165 · `dofus-network/planning` — `.gitignore`, `PIPELINE.md`, `data_splits.py`, +27 more
  > .
- **2026-10-21** `71b7018` · 7 file(s) · +1114 −13 · `dofus-network/tools` — `schema.py`, `kaggle_mcp.py`, `encoder_policy.py`, +4 more
  > .
- **2026-10-21** `d01ffcb` · 18 file(s) · +54230 −17914 · `dofus-network/ai` — `kb.py`, `schema.py`, `gazetteer.py`, +15 more
  > .
- **2026-10-22** `9ba7f87` · 2743 file(s) · +143164 −22865 · `dofus-network/data` — `test_regressions.py`, `fetch_maps.py`, `agentbridge.cs`, +2740 more
  > .
- **2026-10-22** `707fe2b` · 9 file(s) · +1810 −17 · `client/www` — `build.cs`, `mobile-disguise.js`, `autoupdate.js`, +6 more
  > .
- **2026-10-23** `f395b95` · 2 file(s) · +377 −337 · `dofus-network/data` — `val.jsonl`, `train.jsonl`
  > .
- **2026-10-23** `7192ada` · 14 file(s) · +308 −6514 · `client/www` — `CLEANUP.md`, `standalone.html`, `WebView2Backend.cs`, +11 more
  > .
- **2026-10-23** `20d3193` · 65 file(s) · +0 −3387 · `scripts` — `read_js.py`, `find_osa.py`, `list_data.py`, +62 more
  > .
- **2026-10-23** `15429ee` · 2 file(s) · +468 −444 · `dofus-network/data` — `val.jsonl`, `train.jsonl`
  > .
- **2026-10-23** `3225932` · 4 file(s) · +7068 −378 · `dofus-network/data` — `mobile-disguise.js`, `mobile-disguise.js`, `val.jsonl`, +1 more
  > .
- **2026-10-23** `626bd35` · 2 file(s) · +356 −334 · `MORNING.md` — `now.md`, `MORNING.md`
  > .
- **2026-10-23** `39214fc` · 3 file(s) · +564 −489 · `dofus-network/data` — `val.jsonl`, `train.jsonl`, `proxy_lifetime.jsonl`
  > .
- **2026-10-23** `4d7e8ee` · 2 file(s) · +785 −2232 · `desktop/client` — `farmengine.cs`, `styles-native.css`
  > .
- **2026-10-24** `51c429c` · 183 file(s) · +276880 −7992 · `dofus-network/kaggle` — `PLAN.md`, `STATUS.md`, `farmengine.cs`, +180 more
  > .
- **2026-10-24** `5e88108` · 16 file(s) · +2920 −907 · `dofus-network/data` — `PLAN.md`, `STATUS.md`, `dofus-net-proxy.exe~`, +13 more
  > .
- **2026-10-25** `af58537` · 322 file(s) · +25227 −868374 · `dofus-network/kaggle` — `js_out.txt`, `start-combat-ai.bat`, `test_regressions.py`, +319 more
  > .
- **2026-10-25** `04301b1` · 101 file(s) · +1137 −3879 · `dofus-network/combat` — `test_regressions.py`, `world.py`, `cli.py`, +98 more
  > .
- **2026-10-25** `bd6a04b` · 35 file(s) · +2223 −410 · `dofus-network/combat` — `build.cs`, `pages.cs`, `win32.cs`, +32 more
  > .
- **2026-10-26** `181c53c` · 40 file(s) · +169852 −458 · `desktop` — `build.cs`, `pages.cs`, `aipanel.cs`, +37 more
  > .
- **2026-10-26** `740e6b6` · 45 file(s) · +659 −1294 · `dofus-network/combat` — `build.cs`, `pages.cs`, `aipanel.cs`, +42 more
  > .
- **2026-10-26** `6c6a416` · 8 file(s) · +170 −261 · `scripts` — `build.cs`, `https-post.js`, `installer.cs`, +5 more
  > .
- **2026-10-26** `8d52205` · 29 file(s) · +729 −150 · `desktop` — `Ini.cs`, `build.cs`, `pages.cs`, +26 more
  > .
- **2026-10-26** `08c1a8c` · 24 file(s) · +2196 −234 · `tools` — `README.md`, `bundle-selftest.txt`, `patch-engine.js`, +21 more
  > .
- **2026-10-26** `5f222f0` · 13 file(s) · +5508 −30 · `golf/dofus-network` — `README.md`, `BRIEF.md`, `bridge.py`, +10 more
  > .
- **2026-10-26** `5dd0c1f` · 11 file(s) · +727 −1861 · `dofus-network/agent` — `README.md`, `BRIEF.md`, `planner.py`, +8 more
  > .
- **2026-10-26** `54ecd23` · 22 file(s) · +1462 −8851 · `golf/dofus-network` — `README.md`, `done.md`, `BRIEF.md`, +19 more
  > .
- **2026-10-26** `f45df41` · 26 file(s) · +5805 −140 · `dofus-network/agent` — `README.md`, `test_group_scan.js`, `mobile-disguise.js`, +23 more
  > .
- **2026-10-27** `0990695` · 62 file(s) · +16126 −410 · `dofus-network/kaggle` — `build.cs`, `aipanel.cs`, `run.py`, +59 more
  > .
- **2026-10-27** `f690da0` · 73 file(s) · +43184 −45064 · `dofus-network/data` — `resume.md`, `win32.cs`, `aipanel.cs`, +70 more
  > .
- **2026-10-28** `cbc0e31` · 106 file(s) · +13422 −407 · `dofus-network/kaggle` — `rules.md`, `README.md`, `resume.md`, +103 more
  > .
- **2026-10-28** `5e84e79` · 158 file(s) · +54812 −265 · `dofus-network/kaggle` — `now.md`, `resume.md`, `.gitignore`, +155 more
  > .
- **2026-10-29** `c875cde` · 137 file(s) · +60082 −736 · `dofus-network/kaggle` — `resume.md`, `aipanel.cs`, `program.cs`, +134 more
  > .
- **2026-10-30** `175314a` · 57 file(s) · +31259 −19946 · `dofus-network/kaggle` — `rules.md`, `README.md`, `resume.md`, +54 more
  > .
- **2026-11-01** `70e3089` · 141 file(s) · +43145 −6659 · `dofus-network/kaggle` — `rules.md`, `README.md`, `resume.md`, +138 more
  > .
- **2026-11-04** `d393f8f` · 118 file(s) · +18560 −2097 · `dofus-network/kaggle` — `README.md`, `resume.md`, `CLEANUP.md`, +115 more
  > .
- **2026-11-04** `2ced4e2` · 33 file(s) · +4829 −2064 · `dofus-network/data` — `compat_probe.js`, `clientbundle.cs`, `run_brain.py`, +30 more
  > .
- **2026-11-04** `3573f43` · 35 file(s) · +1140 −497 · `dofus-network/data` — `README.md`, `resume.md`, `bundle-selftest.txt`, +32 more
  > .
- **2026-11-06** `68b0e36` · 206 file(s) · +54992 −7030 · `dofus-network/data` — `README.md`, `resume.md`, `README.md`, +203 more
  > .
- **2026-11-06** `404cfe2` · 6 file(s) · +188 −78 · `dofus-network/docs` — `CLEANUP.md`, `PLAN.md`, `CURRENT.md`, +3 more
  > .
- **2026-11-07** `457e250` · 103 file(s) · +13510 −6401 · `dofus-network/data` — `TASKS.md`, `README.md`, `resume.md`, +100 more
  > .
- **2026-11-07** `4cb9c53` · 15 file(s) · +81 −31 · `dofus-network/data` — `TASKS.md`, `README.md`, `resume.md`, +12 more
  > .

---

## Part 2 — Surviving history: 189 commits

Oldest first. This is the record of the history as it stands after the rewrite; shas here
are the current ones. Read `git log --oneline` for the same list without the detail.


### 2026-10

- **2026-10-14** `bc975b8` · 1 file(s) · +2 −0 · `.gitattributes` — `.gitattributes`
  > Initial commit
- **2026-10-14** `2c8830d` · 233 file(s) · +133806 −0 · `sdk/emulator` — `.gitignore`, `compile.bat`, `app_icon.ico`, +230 more
  > Update farmengine.cs
- **2026-10-14** `6d22e8b` · 7 file(s) · +959 −329 · `desktop` — `build.cs`, `win32.cs`, `mainform.cs`, +4 more
  > Create project-id
- **2026-10-15** `10c1518` · 198 file(s) · +2645 −117137 · `sdk/emulator` — `build.cs`, `win32.cs`, `mainform.cs`, +195 more
  > Update mobile-disguise.js
- **2026-10-16** `8503d7a` · 10 file(s) · +518 −553 · `desktop` — `build.cs`, `win32.cs`, `hardware.cs`, +7 more
  > Update advancedFeatures.ini
- **2026-10-16** `016c0c7` · 1 file(s) · +46 −37 · `desktop` — `farmengine.cs`
  > Update farmengine.cs
- **2026-10-17** `43c7f58` · 13 file(s) · +593 −133 · `desktop` — `build.cs`, `win32.cs`, `mainform.cs`, +10 more
  > Revert "."
- **2026-10-17** `c825fe1` · 53 file(s) · +402 −2062 · `sdk-stripped-backup/emulator` — `test-smp.ps1`, `compare-cores.ps1`, `test-blackbox.ps1`, +50 more
  > Reapply "."
- **2026-10-17** `6a42a0a` · 53 file(s) · +402 −2062 · `sdk-stripped-backup/emulator` — `test-smp.ps1`, `compare-cores.ps1`, `test-blackbox.ps1`, +50 more
  > last version
- **2026-10-17** `4b8eea1` · 0 file(s) · +0 −0 · `—` — 
  > Merge branch 'main' of https://github.com/saoxdxd2/dofus-emu
- **2026-10-17** `afc9544` · 1 file(s) · +2 −2 · `desktop` — `farmengine.cs`
  > Update farmengine.cs
- **2026-10-17** `0745ece` · 209 file(s) · +36121 −9146 · `desktop/client` — `TASK_MEMORY.md`, `MASTER_INDEX.md`, `build.cs`, +206 more
  > Update Colab notebook clone cell with dofus-network subfolder fallback
- **2026-10-17** `993b65e` · 1 file(s) · +12 −13 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update Colab notebook clone cell to target dofus-emu repo
- **2026-10-17** `4f0297f` · 1 file(s) · +100 −11 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update train_networkhealth_t4.ipynb
- **2026-10-17** `ab9524c` · 5 file(s) · +4838 −107 · `dofus-network/networkhealth_artifacts` — `.gitignore`, `atlas.py`, `train_networkhealth_t4.ipynb`, +2 more
  > Include pre-prepared features.npz, fix Atlas sort=-id, and fix Drive mount in Colab
- **2026-10-17** `ce9b54b` · 6 file(s) · +156837 −4856 · `dofus-network/networkhealth` — `cli.py`, `data.py`, `atlas.py`, +3 more
  > Scale to 143k real windows: parallel collector, ongoing recurring measurements, seq-len 128
- **2026-10-17** `d07e03d` · 2 file(s) · +293 −19 · `dofus-network/colab` — `train_gpu.py`, `train_networkhealth_t4.ipynb`
  > Check GradScaler scale before stepping scheduler to silence UserWarning
- **2026-10-17** `b51d0f3` · 5 file(s) · +420 −212 · `dofus-network/networkhealth` — `train_gpu.py`, `model.py`, `trm_moe.py`, +2 more
  > Implement TRM-MoE (Dual-Cycle h_cycle/L_cycle + Sparse Mixture of Experts)
- **2026-10-17** `fb8c175` · 1 file(s) · +201 −12 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update train_networkhealth_t4.ipynb
- **2026-10-17** `3974602` · 1 file(s) · +43 −11 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update train_networkhealth_t4.ipynb
- **2026-10-17** `1cdfb37` · 1 file(s) · +39 −0 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update train_networkhealth_t4.ipynb
- **2026-10-17** `367bc55` · 1 file(s) · +0 −18 · `dofus-network/colab` — `train_networkhealth_t4.ipynb`
  > Update train_networkhealth_t4.ipynb
- **2026-10-17** `d8796e5` · 50 file(s) · +30972 −17 · `runtimes/python` — `.gitignore`, `build.cs`, `mainform.cs`, +47 more
  > Add self-contained portable Python, Singleton AI Sentinel, and AI Commander UI
- **2026-10-17** `5a751b6` · 5 file(s) · +280 −41 · `.gitignore` — `.gitignore`, `build.cs`, `requirements.txt`, +2 more
  > Integrate doofz/THX-01 decision agent into Singleton Sentinel and add Colab fine-tuning notebook
- **2026-10-17** `624bbb8` · 9 file(s) · +726 −101 · `desktop` — `build.cs`, `requirements.txt`, `installer.cs`, +6 more
  > Strip out Laya, add natural click/double-click action handlers, and add rich FR/ES/EN Dofus dataset
- **2026-10-17** `c6f1a8f` · 7 file(s) · +1511 −27 · `dofus-network/combat` — `mobile-disguise.js`, `test_combat_and_data.py`, `__init__.py`, +4 more
  > Implement dynamic Dofus Touch damage math, trilingual asset database, and combat rotation optimizer
- **2026-10-17** `8f53dd4` · 10 file(s) · +74765 −14 · `dofus-network/data` — `mobile-disguise.js`, `test_combat_and_data.py`, `tactical_engine.py`, +7 more
  > Implement Tactical Combat Engine (boosts, traps, shields), official encyclopedia sync, and 1,500 scenario policy dataset
- **2026-10-17** `7f0df4b` · 7 file(s) · +799 −0 · `dofus-network/combat` — `__init__.py`, `world_model.py`, `episodic_memory.py`, +4 more
  > Implement ACL 2026 cognitive combat architecture: World Model, TRM-ACT recursive reasoner, and Episodic Skill Memory
- **2026-10-17** `93a0bc8` · 1 file(s) · +324 −111 · `notebooks` — `Fine_Tune_Dofus_THX01.ipynb`
  > Update Fine_Tune_Dofus_THX01.ipynb with complete PyTorch training for ModernBERT Sentinel and TRM-ACT combat policy
- **2026-10-17** `566035c` · 14 file(s) · +343463 −29897 · `dofus-network/navigation` — `.gitignore`, `test_commander_pipeline.py`, `test_world_graph_and_nlp.py`, +11 more
  > Implement spatial navigation, resource graph mapping, Papycha quest intelligence, and trilingual NLP engine
- **2026-10-17** `b4351a6` · 3 file(s) · +160 −1 · `dofus-network/data` — `test_combat_and_data.py`, `teacher_distillation.py`, `episodic_skills.json`
  > Implement 7B teacher model knowledge distillation and synthetic dataset generation pipeline
- **2026-10-17** `30bba16` · 5 file(s) · +609 −1 · `dofus-network/ai` — `__init__.py`, `test_dofus_native_ai.py`, `dofus_native_model.py`, +2 more
  > Implement 100% domain-native Dofus Transformer and self-distillation pipeline (zero external generalist LLM dependency)
- **2026-10-17** `fd01150` · 3 file(s) · +87 −10 · `dofus-network/ai` — `test_dofus_native_ai.py`, `dofus_native_model.py`, `episodic_skills.json`
  > Update episodic_skills.json
- **2026-10-17** `4e4e5f4` · 4 file(s) · +1152 −155 · `notebooks` — `WebView2ClientForm.cs`, `Fine_Tune_Dofus_THX01.ipynb`, `generate_domain_distillation_notebook.py`, +1 more
  > Update Colab notebook with 2-stage domain distillation and wire in-game WebView2 chat NLP bridge
- **2026-10-17** `a305793` · 1 file(s) · +21 −0 · `SECURITY.md` — `SECURITY.md`
  > Create SECURITY.md for security policy
- **2026-10-17** `d206707` · 11 file(s) · +128659 −141956 · `dofus-network/ai` — `.gitignore`, `test_dofus_native_ai.py`, `Fine_Tune_Dofus_THX01.ipynb`, +8 more
  > Implement rigorous Dofus Knowledge Certification Benchmark, DKD self-distillation, and 30m convergence training
- **2026-10-17** `2dc504b` · 50 file(s) · +6686 −1554 · `desktop/client` — `.gitignore`, `mobile-disguise.js`, `cordova.js`, +47 more
  > Update Dofus Touch client assets to 3.2.7, align spoofing version, and enforce local-only credentials/stats privacy
- **2026-10-17** `7a30ed3` · 4 file(s) · +72591 −92743 · `dofus-network/data` — `Fine_Tune_Dofus_THX01.ipynb`, `generate_domain_distillation_notebook.py`, `generate_expanded_corpus.py`, +1 more
  > fix(ai): balance combat policy dataset and tune stance loss weighting
- **2026-10-17** `fc9d505` · 5 file(s) · +438 −7 · `desktop/client` — `mobile-disguise.js`, `WebView2ClientForm.cs`, `standalone.html`, +2 more
  > fix(client): update appVersion to 3.14.2, set window.appInfo, and enable web security bypass for OAuth token exchange
- **2026-10-17** `fdf63b6` · 14 file(s) · +537 −32 · `desktop` — `.gitignore`, `build.cs`, `mainform.cs`, +11 more
  > feat(client): implement dynamic multi-tier version resolver and live store sync
- **2026-10-17** `cc9a510` · 44 file(s) · +3645 −212 · `tests` — `.gitignore`, `build.cs`, `_apk_out.txt`, +41 more
  > Merge remote-tracking branch 'origin/main' into cline/35006
- **2026-10-17** `238d16a` · 38 file(s) · +452544 −149906 · `dofus-network/data` — `hardware.cs`, `mainform.cs`, `farmengine.cs`, +35 more
  > fix(client): auto-zoom stability, unthrottled multi-instance performance, and combat test suite
- **2026-10-17** `2380ace` · 4 file(s) · +26 −15 · `desktop/client` — `mobile-disguise.js`, `WebView2ClientForm.cs`, `script.js`, +1 more
  > fix(client): fix atlas slice load notification to resolve map change stall and restore direct network streaming
- **2026-10-17** `fb35cda` · 12 file(s) · +635 −177 · `desktop/client` — `mainform.cs`, `patch-engine.js`, `js-error-finder.js`, +9 more
  > fix(client): restore loading egg progress assets, transition pipeline, and sync Go proxy
- **2026-10-19** `157ac9d` · 212 file(s) · +123324 −8324 · `client/www` — `.go-version`, `SECURITY.md`, `compile.bat`, +209 more
  > done
- **2026-10-21** `37839de` · 173 file(s) · +130370 −25 · `scripts` — `valid`, `.gitignore`, `build.cs`, +170 more
  > Fix encoder inference tool field, THX corruption, add regression tests
- **2026-10-21** `aac6fe6` · 0 file(s) · +0 −0 · `—` — 
  > Merge branch 'cline/058da'
- **2026-10-22** `fae3915` · 2795 file(s) · +195747 −26001 · `dofus-network/data` — `valid`, `README.md`, `.gitignore`, +2792 more
  > Proxy supervisor, transactional updater, verifier pipeline; untrack credentials
- **2026-10-22** `3379f67` · 148 file(s) · +1680 −335 · `.git-rewrite/map` — `README.md`, `HANDOVER.md`, `revs`, +145 more
  > Agent 2 controller closes the loop; weighted labels; version resolution; measured RSS
- **2026-10-22** `8edce78` · 130 file(s) · +3 −308 · `.git-rewrite/map` — `.gitignore`, `revs`, `heads`, +127 more
  > Remove git-rewrite scratch from the tree
- **2026-10-22** `06710dc` · 1 file(s) · +3 −0 · `.gitignore` — `.gitignore`
  > Remove git-rewrite scratch from the tree
- **2026-10-22** `cc32360` · 18 file(s) · +408 −21 · `desktop` — `build.cs`, `aipanel.cs`, `program.cs`, +15 more
  > Fix the four errors in the log: STA dispatch, stale stop file, writer key, cache-buster
- **2026-10-22** `3c893f4` · 6 file(s) · +126 −8 · `dofus-network/agent` — `aipanel.cs`, `clientbundle.cs`, `autopilot.py`, +3 more
  > Updater timeout+retry, prefer ONNX head, controller sees unexpressable goals
- **2026-10-22** `684b486` · 3 file(s) · +29 −4 · `.workbuddy-ai/memory` — `program.cs`, `ai-status-selftest.txt`, `2026-10-22.md`
  > Restore [STAThread] on Main: the attribute had drifted onto a helper, so the process ran MTA and WebView2 refused to start
- **2026-10-22** `d45e759` · 11 file(s) · +53 −154 · `tools` — `bundle-selftest.txt`, `patch-engine.js`, `patch-rules.txt`, +8 more
  > Black screen: writer stub needs addChannel, guard menuBar, drop the shortcutBarManager shim
- **2026-10-22** `bbf56e7` · 5 file(s) · +63 −7 · `dofus-network/agent` — `runtime.py`, `verifier.py`, `autopilot.py`, +2 more
  > AI: queries are not failures, inventory goals parse, and unsupported goals say so
- **2026-10-22** `f375d17` · 4 file(s) · +104 −4 · `dofus-network/agent` — `verifier.py`, `autopilot.py`, `2026-10-22.md`, +1 more
  > Rules outrank a stale model on read-only tools; step line shows what came back; retrain head
- **2026-10-22** `47695d1` · 2 file(s) · +65 −5 · `.workbuddy-ai/memory` — `autopilot.py`, `2026-10-22.md`
  > Queries finish their goal in one call; bridge probe needs a response; step latency excludes the observation
- **2026-10-22** `c3d04c8` · 9 file(s) · +1544 −0 · `dofus-network/agent` — `run.py`, `world.py`, `planner.py`, +6 more
  > Agent 2: KB planner, symbolic world, scoring harness, planner corpus (21 quests vs 0)
- **2026-10-22** `229890e` · 14 file(s) · +1229 −446 · `dofus-network/agent` — `README.md`, `aipanel.cs`, `schema.py`, +11 more
  > Knowledge store + remember/forget tools; planner reads discoveries; idle trainer
- **2026-10-22** `f9cb128` · 11 file(s) · +985 −2 · `dofus-network/agent` — `run.py`, `autopilot.py`, `self_improve.py`, +8 more
  > Learned tool choice replaces the if-chain; resume-and-improve loop with calibration
- **2026-10-22** `a45b5df` · 6 file(s) · +346 −0 · `dofus-network/kaggle` — `.gitignore`, `2026-10-22.md`, `train_trm.py`, +3 more
  > Kaggle pipeline: private planner corpus + TRM repair kernel (recursive, deep supervision)
- **2026-10-22** `9a99d02` · 5 file(s) · +289 −15 · `dofus-network/kaggle` — `.gitignore`, `rollout.py`, `2026-10-22.md`, +2 more
  > Rollout corpus generator (99k rows, outcomes, injected failures) and scaled TRM v2
- **2026-10-22** `39f8d7d` · 9 file(s) · +75147 −4 · `dofus-network/kaggle` — `rollout.py`, `2026-10-22.md`, `train_seq.py`, +6 more
  > Plan-chunk corpus (67k windows) and kernel; trainers locate their corpus instead of assuming the mount path
- **2026-10-22** `0aee9e5` · 3 file(s) · +76 −2 · `dofus-network/kaggle` — `2026-10-22.md`, `train_trm.py`, `kernel-metadata-v3.json`
  > Kaggle mounts private data at /kaggle/input/datasets/<owner>/<slug>; fix batch-shape crash in TRM
- **2026-10-22** `e7a8899` · 4 file(s) · +183 −3 · `dofus-network/kaggle` — `kaggle_report.py`, `2026-10-22.md`, `train_seq.py`, +1 more
  > Kaggle run tracker; fix per-step projection in the plan-chunk model
- **2026-10-22** `3d4d3ed` · 12 file(s) · +1340 −4 · `dofus-network/kaggle` — `.gitignore`, `world.py`, `rollout.py`, +9 more
  > Results: TRM recursion is 1.0 at k=1 (rejected); chunk 0.968 exact. Build a noisy world to make the task hard
- **2026-10-22** `43097c1` · 5 file(s) · +297 −0 · `dofus-network/sim` — `.gitignore`, `world.py`, `decisions.py`, +2 more
  > Counterfactual decision corpus: retry is the worst option after a failure (measured)
- **2026-10-22** `9eb7285` · 4 file(s) · +235 −0 · `dofus-network/agent` — `controller.py`, `reality_check.py`, `2026-10-22.md`, +1 more
  > Reality check: 14/15 real failures were structural; stop retrying what cannot succeed
- **2026-10-22** `0972299` · 8 file(s) · +1070 −11 · `dofus-network/agent` — `train_value.py`, `controller.py`, `KAGGLE_RUNS.md`, +5 more
  > Recursion rejected on both worlds; three-way error taxonomy; value model fails to beat a constant
- **2026-10-22** `0d6d34d` · 2 file(s) · +211 −0 · `.workbuddy-ai/memory` — `now.md`, `2026-10-22.md`
  > now.md: full current state, what is dead, and the ordered plan
- **2026-10-22** `5c4f3e3` · 46 file(s) · +12170 −209 · `dofus-network/kaggle` — `now.md`, `.gitignore`, `run.py`, +43 more
  > The world was lying: fix four sim bugs, then the tool policy beats the teacher
- **2026-10-22** `63d54c0` · 6 file(s) · +266 −24 · `dofus-network/ai` — `now.md`, `ab_scores.py`, `tool_choice.py`, +3 more
  > The ceiling is the tie-break: 24% of labels are a coin flip I wrote
- **2026-10-22** `a93a366` · 2 file(s) · +59 −7 · `dofus-network/kaggle` — `now.md`, `run.py`
  > Kaggle output was 134 MB because the corpus lived in /kaggle/working
- **2026-10-22** `400d49d` · 5 file(s) · +141 −9 · `dofus-network/sim` — `now.md`, `run.py`, `world.py`, +2 more
  > Travel was free, which is why the teacher was unbeatable
- **2026-10-22** `d023892` · 7 file(s) · +303 −112 · `dofus-network/sim` — `now.md`, `run.py`, `world.py`, +4 more
  > The tool's argument now names the objective
- **2026-10-22** `9bb311c` · 8 file(s) · +35786 −21 · `dofus-network/agent` — `now.md`, `features.py`, `eval_travel.py`, +5 more
  > The learned tool policy was winning because its mistakes were cheap
- **2026-10-22** `bd9a40f` · 7 file(s) · +2894 −61 · `dofus-network/data` — `now.md`, `world.py`, `decisions.py`, +4 more
  > The repair decision has no learnable content, and the reason is structural
- **2026-10-22** `1abbff2` · 4 file(s) · +108 −7 · `dofus-network/agent` — `now.md`, `autopilot.py`, `controller.py`, +1 more
  > A live session with a deterministic rule logs nothing learnable
- **2026-10-23** `902be92` · 7 file(s) · +449 −1 · `dofus-network/agent` — `now.md`, `recorder.py`, `autopilot.py`, +4 more
  > A session log now becomes a dataset, or says why it cannot
- **2026-10-23** `124a1e0` · 9 file(s) · +236 −30 · `dofus-network/agent` — `aipanel.cs`, `agentbridge.cs`, `bridge.py`, +6 more
  > The app was running with no bridge: port 8893 was taken and nothing said so
- **2026-10-23** `5ae2613` · 14 file(s) · +205 −28 · `dofus-network/agent` — `aipanel.cs`, `agentbridge.cs`, `mobile-disguise.js`, +11 more
  > The user was right: nav.goto did not exist, and the panel had no planner
- **2026-10-23** `7698418` · 8 file(s) · +231 −17 · `desktop` — `aipanel.cs`, `mainform.cs`, `mobile-disguise.js`, +5 more
  > quest.advance ignored npc_id, and shortcuts only worked when the app had focus
- **2026-10-23** `4047bea` · 4 file(s) · +45 −6 · `client/www` — `mobile-disguise.js`, `mobile-disguise.js`, `autopilot.py`, +1 more
  > The character is not in the world: that is why nothing works
- **2026-10-23** `d5fa4b7` · 7 file(s) · +436 −88 · `.workbuddy-ai/memory` — `mobile-disguise.js`, `mobile-disguise.js`, `probe_client.py`, +4 more
  > The snapshot reader read fields that do not exist in this client build
- **2026-10-23** `ba89de7` · 9 file(s) · +318 −87 · `desktop` — `aipanel.cs`, `mainform.cs`, `WebView2Backend.cs`, +6 more
  > The in-game badge is now the AI button, and two fake settings are gone
- **2026-10-23** `2ae9ec5` · 5 file(s) · +133 −27 · `.workbuddy-ai/memory` — `mobile-disguise.js`, `mobile-disguise.js`, `probe_client.py`, +2 more
  > The reader works: Aiismine, level 18, map 150736645 at cell 328
- **2026-10-23** `4befeb4` · 6 file(s) · +134 −19 · `.workbuddy-ai/memory` — `mobile-disguise.js`, `WebView2ClientForm.cs`, `mobile-disguise.js`, +3 more
  > The "old and new code" is two message conventions, and half of them were dropped
- **2026-10-23** `7b146f7` · 6 file(s) · +247 −42 · `dofus-network/agent` — `planner.py`, `autopilot.py`, `2026-10-22.md`, +3 more
  > Cold read: the loop was designed for one order, not a standing goal
- **2026-10-23** `cb09a6e` · 5 file(s) · +152 −40 · `.workbuddy-ai/memory` — `mobile-disguise.js`, `mobile-disguise.js`, `2026-10-22.md`, +2 more
  > The button now says what happened, and the capability gap is written down
- **2026-10-23** `c4b93b4` · 6 file(s) · +446 −23 · `dofus-network/agent` — `mobile-disguise.js`, `verifier.py`, `mobile-disguise.js`, +3 more
  > The verifier was reading an observation shape that no longer exists
- **2026-10-23** `4afd09e` · 5 file(s) · +63 −5 · `dofus-network/agent` — `agentbridge.cs`, `serve.py`, `autopilot.py`, +2 more
  > The port fallback never reached Python: --directive-url had a hardcoded default
- **2026-10-23** `8d884bc` · 9 file(s) · +1129 −107 · `desktop` — `aipanel.cs`, `mainform.cs`, `mobile-disguise.js`, +6 more
  > Read the client instead of guessing: NPCs, monster groups, movement, reload
- **2026-10-23** `4c124a6` · 4 file(s) · +97 −6 · `client/www` — `mobile-disguise.js`, `verify_live.py`, `mobile-disguise.js`, +1 more
  > quest.advance walks to the NPC before talking
- **2026-10-23** `53f4501` · 5 file(s) · +180 −0 · `dofus-network/agent` — `mobile-disguise.js`, `planner.py`, `mobile-disguise.js`, +2 more
  > The observation carried no quest state at all
- **2026-10-23** `c8eb3fd` · 5 file(s) · +240 −3 · `MORNING.md` — `MORNING.md`, `mobile-disguise.js`, `planner.py`, +2 more
  > Kill objectives: attack the group that actually contains the monster
- **2026-10-23** `806bb4f` · 4 file(s) · +21 −2 · `client/www` — `mobile-disguise.js`, `mobile-disguise.js`, `autopilot.py`, +1 more
  > Guard rails for an unattended run
- **2026-10-23** `24be962` · 4 file(s) · +120 −0 · `client/www` — `mobile-disguise.js`, `verify_live.py`, `mobile-disguise.js`, +1 more
  > Cross-map routing: find out what the client knows before building on it
- **2026-10-23** `e2c9560` · 1 file(s) · +25 −0 · `now.md` — `now.md`
  > now.md points at MORNING.md and records the night's findings
- **2026-10-23** `f6ec695` · 2 file(s) · +58 −1 · `.workbuddy-ai/memory` — `verify_live.py`, `2026-10-22.md`
  > verify_live: one true failure instead of three misleading ones
- **2026-10-23** `0b6c24e` · 15 file(s) · +520 −6867 · `client/www` — `build.cs`, `standalone.html`, `WebView2Backend.cs`, +12 more
  > The client agent ran twice per page load, and there were three copies of it
- **2026-10-23** `dfaf093` · 69 file(s) · +410 −3388 · `scripts` — `CLEANUP.md`, `read_js.py`, `find_osa.py`, +66 more
  > Remove the experiment debris, and pin the fixes so they cannot silently come back
- **2026-10-23** `7da1b9a` · 8 file(s) · +337 −28 · `dofus-network/agent` — `CLEANUP.md`, `world.py`, `mobile-disguise.js`, +5 more
  > The contract rejected the plan, so nothing reached the game
- **2026-10-23** `9cffe74` · 1 file(s) · +327 −327 · `dofus-network/data` — `train.jsonl`
  > idle trainer regenerated its dataset from the last session
- **2026-10-23** `929244b` · 6 file(s) · +271 −2 · `desktop` — `CLEANUP.md`, `mainform.cs`, `mobile-disguise.js`, +3 more
  > A live session could be rebuilt out from under the client, and the reason was never logged
- **2026-10-23** `0e10aad` · 2 file(s) · +448 −377 · `dofus-network/data` — `val.jsonl`, `train.jsonl`
  > idle trainer regenerated its datasets again (runtime output)
- **2026-10-23** `563d585` · 0 file(s) · +0 −0 · `—` — 
  > Merge branch 'main' of https://github.com/saoxdxd2/dofus-emu
- **2026-10-23** `1ece59d` · 7 file(s) · +964 −446 · `dofus-network/agent` — `CLEANUP.md`, `aipanel.cs`, `verifier.py`, +4 more
  > Every action was scored noop: a walk was judged before it moved
- **2026-10-23** `2792018` · 11 file(s) · +553 −13 · `dofus-network/agent` — `CLEANUP.md`, `bridge.py`, `observe.py`, +8 more
  > The agent's own calls were going through your VPN proxy, and the planner was blind
- **2026-10-23** `1d1a5b6` · 2 file(s) · +373 −488 · `dofus-network/data` — `val.jsonl`, `train.jsonl`
  > idle trainer regenerated its datasets (runtime output)
- **2026-10-23** `a6b4646` · 5 file(s) · +56 −2 · `dofus-network/agent` — `aipanel.cs`, `WebView2ClientForm.cs`, `verifier.py`, +2 more
  > Claiming an objective scored noop, so real progress was thrown away
- **2026-10-23** `eff6a70` · 6 file(s) · +7098 −382 · `desktop` — `WebView2Backend.cs`, `WebView2ClientForm.cs`, `mobile-disguise.js`, +3 more
  > The window is closed by a click, and every close was logged identically
- **2026-10-23** `0e4ab14` · 2 file(s) · +0 −6550 · `client/www` — `mobile-disguise.js`, `mobile-disguise.js`
  > Remove the two copies Build.exe recreated again (stale binary, see builder/build.cs)
- **2026-10-23** `a78fe37` · 3 file(s) · +607 −334 · `MORNING.md` — `now.md`, `MORNING.md`, `test_egress.py`
  > The emulator is fine; the egress is gone
- **2026-10-23** `0152998` · 3 file(s) · +113 −2 · `MORNING.md` — `MORNING.md`, `mobile-disguise.js`, `verify_live.py`
  > The talk chain now says which step it stopped at, and stops replying into an empty dialog
- **2026-10-23** `9902fab` · 1 file(s) · +482 −482 · `dofus-network/data` — `train.jsonl`
  > idle trainer regenerated its dataset (runtime output)
- **2026-10-23** `00d92a3` · 8 file(s) · +241 −75777 · `.gitignore` — `now.md`, `.gitignore`, `MORNING.md`, +5 more
  > The egress automation existed, worked, and was never wired or shipped
- **2026-10-23** `c01203e` · 6 file(s) · +401 −14 · `MORNING.md` — `now.md`, `MORNING.md`, `build.cs`, +3 more
  > A real-time harvester, and WARP answered with handshakes instead of guesses
- **2026-10-23** `debb2f6` · 2 file(s) · +52 −21 · `MORNING.md` — `now.md`, `MORNING.md`
  > ping 1.1.1.1 proves nothing, and the block is narrower than I said
- **2026-10-23** `7f704e8` · 6 file(s) · +192 −10 · `desktop` — `now.md`, `MORNING.md`, `farmengine.cs`, +3 more
  > The emulator may use the network; it may not change it
- **2026-10-23** `5520967` · 5 file(s) · +330 −6 · `dofus-network/ai` — `MORNING.md`, `build.cs`, `manage-proxy.bat`, +2 more
  > A management entry point for the installed copy, not just a launcher
- **2026-10-23** `a119ef1` · 2 file(s) · +35 −3 · `scripts` — `proxy_pool.js`, `harvest_proxies.js`
  > The queue was being used backwards, and that was the whole problem
- **2026-10-23** `3c81bfb` · 3 file(s) · +365 −0 · `kaggle/networkhealth` — `train.py`, `score_proxies.py`, `kernel-metadata.json`
  > Retrain the network-health model on Kaggle, and make it judge the pool
- **2026-10-23** `5ef4dc6` · 4 file(s) · +55 −14 · `builder` — `now.md`, `build.cs`, `score_proxies.py`, +1 more
  > The retrained model loads, and the tool that uses it now knows its own limits
- **2026-10-23** `13c254a` · 2 file(s) · +193 −2 · `dofus-network/ai` — `collect_local.py`, `cli.py`
  > Collect local telemetry at the cadence the model is actually used at
- **2026-10-23** `f001745` · 2 file(s) · +61 −16 · `dofus-network/ai` — `now.md`, `score_proxies.py`
  > Phase 5: a corpus from this machine, at the cadence it is used at
- **2026-10-23** `7a0a2ec` · 6 file(s) · +173 −24 · `dofus-network/ai` — `MORNING.md`, `build.cs`, `score_proxies.py`, +3 more
  > One resolver for the checkpoint, because two tools disagreed about which model to use
- **2026-10-23** `a94c255` · 2 file(s) · +20 −3 · `dofus-network/ai` — `manage-proxy.bat`, `score_proxies.py`
  > `manage-proxy.bat rank`, and a ranking order that survives contact with the data
- **2026-10-23** `288f914` · 1 file(s) · +61 −0 · `manage-proxy.bat` — `manage-proxy.bat`
  > The half of "auto" that was missing: nobody refilled the pool
- **2026-10-23** `c9e9a8b` · 4 file(s) · +381 −58 · `dofus-network/ai` — `manage-proxy.bat`, `start-network.bat`, `pool_runner.py`, +1 more
  > The pool now runs on the interpreter the installer actually ships
- **2026-10-23** `b39f4e5` · 2 file(s) · +46 −3 · `desktop` — `pages.cs`, `proxysupervisor.cs`
  > The fatal error: a null candidate slot, read from a pool thread
- **2026-10-23** `5657db2` · 1 file(s) · +18 −4 · `dofus-network/agent` — `smoke_verifier.py`
  > The duplicate-copy check was testing the wrong invariant
- **2026-10-23** `946158e` · 1 file(s) · +16 −1 · `desktop` — `proxysupervisor.cs`
  > Every probe miss rebuilt the relay, which is what killed the game connection
- **2026-10-23** `83a8e01` · 2 file(s) · +92 −4 · `desktop` — `now.md`, `proxysupervisor.cs`
  > A flapping tunnel outranked every stable proxy, and the ranking never knew
- **2026-10-23** `870ce60` · 3 file(s) · +156 −23 · `desktop` — `farmengine.cs`, `proxysupervisor.cs`, `dofus-net-proxy.go`
  > The relay fails over per dial, so a dying proxy costs a retry instead of the session
- **2026-10-23** `e4db6d3` · 2 file(s) · +57 −12 · `desktop` — `proxysupervisor.cs`, `dofus-net-proxy.go`
  > One exit at a time: sticky egress, changed only when it fails
- **2026-10-23** `20bcc99` · 1 file(s) · +73 −16 · `scripts` — `dofus-net-proxy.go`
  > Race the upstreams to establish an exit, then pin it
- **2026-10-23** `e39aac6` · 3 file(s) · +234 −17 · `desktop` — `farmengine.cs`, `proxysupervisor.cs`, `dofus-net-proxy.go`
  > The trick: one fixed exit, many disposable hops
- **2026-10-23** `d04c0af` · 3 file(s) · +234 −49 · `desktop` — `farmengine.cs`, `proxysupervisor.cs`, `dofus-net-proxy.go`
  > Three real bugs in the chain, and an honest retreat from shipping it
- **2026-10-23** `29ad616` · 2 file(s) · +50 −6 · `desktop` — `farmengine.cs`, `proxysupervisor.cs`
  > The chain's problem was the hop's scheme, not its availability
- **2026-10-23** `6b35f1e` · 1 file(s) · +110 −1 · `scripts` — `dofus-net-proxy.go`
  > The hop index survives restarts, so the first dial is never blind again
- **2026-10-23** `f4571a5` · 1 file(s) · +73 −9 · `scripts` — `dofus-net-proxy.go`
  > A dead exit was stalling every request, and my startup check was too weak to notice
- **2026-10-23** `16c4405` · 2 file(s) · +23 −2 · `dofus-network/agent` — `dofus-net-proxy.go`, `autopilot.py`
  > Bound the relay's heap, and stop scoring the first action as a no-op
- **2026-10-23** `c246940` · 1 file(s) · +18 −0 · `scripts` — `dofus-net-proxy.go`
  > The relay was changing the exit IP under a live session, which is what got you kicked
- **2026-10-23** `d2f811a` · 1 file(s) · +14 −7 · `desktop` — `proxysupervisor.cs`
  > Every kick landed on the WARP tunnel, so proxies now outrank it
- **2026-10-23** `0e14220` · 2 file(s) · +88 −2 · `desktop` — `proxysupervisor.cs`, `dofus-net-proxy.go`
  > The egress is pinned across restarts, and the GC no longer taxes long sessions
- **2026-10-23** `b704220` · 2 file(s) · +55 −2 · `desktop` — `mainform.cs`, `farmengine.cs`
  > Every WebView2 launch was a brand-new phone, which is why the account kept getting closed
- **2026-10-23** `41e9078` · 3 file(s) · +6736 −0 · `client/www` — `mobile-disguise.js`, `mobile-disguise.js`, `mobile-disguise.js`
  > The canvas fingerprint changed when you read it twice
- **2026-10-23** `2d4595d` · 4 file(s) · +48 −95 · `scripts` — `compile.bat`, `go.mod`, `go.sum`, +1 more
  > SOCKS5 comes from x/net/proxy now, and compile.bat actually builds the relay
- **2026-10-23** `b0007e2` · 3 file(s) · +36 −15 · `desktop` — `mainform.cs`, `farmengine.cs`, `proxysupervisor.cs`
  > Never rebuild a relay on an egress change, and stop paying for fixed-exit validation
- **2026-10-23** `e54a1b3` · 2 file(s) · +69 −1 · `desktop` — `farmengine.cs`, `dofus-net-proxy.go`
  > Asset downloads get their own egress, because one proxy is a queue
- **2026-10-23** `191b628` · 2 file(s) · +69 −16 · `desktop` — `farmengine.cs`, `dofus-net-proxy.go`
  > Asset downloads fan out across a pool of egresses, not one
- **2026-10-23** `a0218fb` · 1 file(s) · +28 −1 · `scripts` — `dofus-net-proxy.go`
  > Count cache hits and misses, because "how much can we serve directly" was a guess
- **2026-10-23** `6f06ebb` · 2 file(s) · +40 −25 · `desktop` — `farmengine.cs`, `netsettings.cs`
  > Sticky session is one setting away: exit_proxy in network.ini
- **2026-10-23** `61cde87` · 2 file(s) · +153 −0 · `desktop` — `proxysupervisor.cs`, `rank_proxies.py`
  > Rank the pool by survival, not by one latency sample
- **2026-10-23** `df738d4` · 2 file(s) · +59 −0 · `dofus-network/ai` — `dofus-net-proxy.go`, `rank_proxies.py`
  > A local exit cannot be reached through a remote hop, and the ranker now writes training rows
- **2026-10-23** `e9d8714` · 3 file(s) · +77 −0 · `desktop` — `mainform.cs`, `proxysupervisor.cs`, `WebView2ClientForm.cs`
  > The app learns which egress survives here, because users cannot be asked to configure one
- **2026-10-23** `05de940` · 4 file(s) · +982 −2656 · `scripts` — `go.work`, `go.mod`, `go.sum`, +1 more
  > Their rewrite compiles and runs; two build problems were hiding behind the module errors
- **2026-10-23** `1e85ca9` · 1 file(s) · +589 −220 · `scripts` — `dofus-net-proxy.go`
  > Remove the warm-pool leftovers and gofmt the relay
- **2026-10-23** `2f41289` · 4 file(s) · +585 −489 · `dofus-network/data` — `proxysupervisor.cs`, `val.jsonl`, `train.jsonl`, +1 more
  > "Servers under maintenance" was the only upstream dying five seconds in
- **2026-10-23** `1167de1` · 1 file(s) · +10 −1 · `scripts` — `dofus-net-proxy.go`
  > A 403 from this CDN means the map is not there, not that we are blocked
- **2026-10-23** `fc6a26f` · 1 file(s) · +29 −0 · `scripts` — `dofus-net-proxy.go`
  > Skype telemetry was moving the game's exit IP, which is what got you kicked
- **2026-10-23** `22b6235` · 1 file(s) · +26 −0 · `scripts` — `dofus-net-proxy.go`
  > One session, one address: the first game dial now picks the egress alone
- **2026-10-23** `1e2a413` · 2 file(s) · +223 −0 · `PLAN.md` — `PLAN.md`, `STATUS.md`
  > Write down where this stands and what comes next
- **2026-10-26** `da440e8` · 423 file(s) · +430151 −835963 · `dofus-network/kaggle` — `now.md`, `PLAN.md`, `README.md`, +420 more
  > documentation update
- **2026-10-26** `41fb0c2` · 32 file(s) · +12212 −251 · `tools` — `README.md`, `bundle-selftest.txt`, `patch-engine.js`, +29 more
  > golf: checkpoint wave 1 artifacts
- **2026-10-26** `07852b4` · 4 file(s) · +426 −21 · `golf` — `done.md`, `BRIEF.md`, `EXPLANATION.md`, +1 more
  > golf: ledger, explanation, wave 2 brief
- **2026-10-26** `4d3314e` · 1 file(s) · +4 −1 · `golf` — `done.md`
  > golf: correct rejected-pass ledger note
- **2026-10-26** `b3ad936` · 1 file(s) · +21 −30 · `golf/dofus-network` — `encoder_policy.py`
  > golf: restore proven encoder artifact; reject FLOP-doubling pass
- **2026-10-26** `63e5b10` · 1 file(s) · +19 −10 · `golf/dofus-network` — `encoder_policy.py`
  > golf: merge encoder span pruning without duplicated forward ops
- **2026-10-26** `2948dd7` · 1 file(s) · +737 −0 · `golf/dofus-network` — `fight_estimate.py`
  > golf: add fight_estimate artifact
- **2026-10-26** `a97cfb8` · 2 file(s) · +35 −30 · `golf` — `done.md`, `EXPLANATION.md`
  > golf: update ledger for encoder merge and fight_estimate
- **2026-10-26** `e7ed91e` · 1 file(s) · +185 −0 · `golf/dofus-network` — `schema.py`
  > golf: checkpoint schema artifact (verification pending)

### 2026-11

- **2026-11-06** `c10b63d` · 591 file(s) · +265602 −55462 · `dofus-network/kaggle` — `now.md`, `rules.md`, `README.md`, +588 more
  > docs(fight): record the party WSP verdicts XII.10-XII.14
- **2026-11-06** `53312a7` · 208 file(s) · +61010 −13002 · `dofus-network/data` — `README.md`, `resume.md`, `README.md`, +205 more
  > docs(fight): close three party mechanisms with score-unit measurement (XII.15)
- **2026-11-06** `1e64f3f` · 1 file(s) · +6008 −6008 · `dofus-network/docs` — `ARCH_REASSESSMENT.md`
  > docs(fight): repair doubled CR line endings introduced in the XII.15 append
- **2026-11-06** `b173205` · 2 file(s) · +50 −0 · `dofus-network/docs` — `CURRENT.md`, `ARCH_REASSESSMENT.md`
  > docs(fight): joint spell-target allocation reaches the scripted opening ceiling (XII.16)
- **2026-11-06** `98162aa` · 1 file(s) · +23 −0 · `dofus-network/docs` — `ARCH_REASSESSMENT.md`
  > docs(fight): record the allocation arm's mask caveat and the re-price that did not move it
- **2026-11-06** `80f55ae` · 1 file(s) · +22 −0 · `dofus-network/docs` — `ARCH_REASSESSMENT.md`
  > docs(fight): export the allocation target as labels behind passing mask witnesses (XII.16b)
- **2026-11-06** `b8e2b49` · 1 file(s) · +21 −0 · `dofus-network/docs` — `PLAN.md`
  > docs(fight): hand off the party allocation result with its gate and next commands
- **2026-11-07** `74293f3` · 111 file(s) · +15006 −6605 · `dofus-network/kaggle` — `TASKS.md`, `README.md`, `resume.md`, +108 more
  > stress(serve): harden the agent HTTP control plane against malformed and adversarial traffic
- **2026-11-07** `9953050` · 80 file(s) · +962 −2301 · `dofus-network/docs` — `now.md`, `PLAN.md`, `TASKS.md`, +77 more
  > feat(game-loop): equip slot, dialog stall, menu window learning, and artifact cleanup
- **2026-11-07** `cf05765` · 1 file(s) · +86 −2 · `dofus-network/data` — `capabilities.json`
  > data: update capabilities.json with read_only/moves/lists fields and fix encoding
- **2026-11-07** `6bb3d81` · 10 file(s) · +206 −26 · `dofus-network/kaggle` — `regression.py`, `AI_ARCHITECTURE.md`, `EXPERIENCE_LEARNING.md`, +7 more
  > gate: add tool-class consistency checks between schema, registry, and verifier; update AI_ARCHITECTURE tool count to 28
- **2026-11-07** `9b59495` · 19 file(s) · +171 −53 · `dofus-network/data` — `TASKS.md`, `README.md`, `resume.md`, +16 more
  > test(agent): enforce command-model label sets and align tool vocabulary to 28
- **2026-11-07** `6551ead` · 10 file(s) · +91 −12 · `dofus-network/data` — `schema.py`, `regression.py`, `schema.py`, +7 more
  > chore(agent): regenerate schema, capability and status artifacts
- **2026-11-07** `5dc642e` · 21 file(s) · +215 −26 · `dofus-network/data` — `TASKS.md`, `README.md`, `resume.md`, +18 more
  > docs: archive historical status docs and record the 2026-11-07 hygiene pass
- **2026-11-07** `574d9b6` · 1 file(s) · +8 −5 · `MAINTENANCE.md` — `MAINTENANCE.md`
  > docs(maintenance): record the force-push and its consequence for other clones
- **2026-11-07** `1bfa5e6` · 10 file(s) · +71 −25 · `dofus-network/data` — `TASKS.md`, `README.md`, `resume.md`, +7 more
  > chore(agent): regenerate runtime artifacts

---

## Part 3 — The second pass, 2026-11-07 (13:30)

The first pass removed commits that had no message at all. This pass dealt with commits that
had a message naming nothing: the GitHub web editor's default (`Update farmengine.cs`), a bare
`done`, `last version`, `documentation update`, and a `Revert "."` / `Reapply "."` pair that
cancelled each other out.

**Not all of them were noise.** Four turned out to be large milestones hiding behind a trivial
label — `documentation update` was 423 files and 835,963 deleted lines. Squashing those into a
neighbour would have made the surviving commit's diff enormous and false, so they were **reworded
instead**, keeping the boundary where the work actually happened.

| action | count |
|---|---|
| squashed (small: ≤10 files or a no-op pair) | 6 |
| reworded (large: kept as boundaries, message corrected) | 5 |

History went **190 → 184 commits**. The tree before and after is identical (`3060425…`),
3,967 files either way, `git diff` empty.

### Squashed

| date | sha | subject | files | +/− | scope |
|---|---|---|---|---|---|
| 2026-10-16 | `8503d7a` | Update advancedFeatures.ini | 10 | +518 −553 | `desktop` |
| 2026-10-16 | `016c0c7` | Update farmengine.cs | 1 | +46 −37 | `desktop` |
| 2026-10-17 | `43c7f58` | Revert "." | 13 | +593 −133 | `desktop` |
| 2026-10-17 | `c825fe1` | Reapply "." | 53 | +402 −2062 | `sdk-stripped-backup/emulator` |
| 2026-10-17 | `afc9544` | Update farmengine.cs | 1 | +2 −2 | `desktop` |
| 2026-10-17 | `fd01150` | Update episodic_skills.json | 3 | +87 −10 | `dofus-network/ai` |

What each one touched:

- **2026-10-16** `8503d7a` — *Update advancedFeatures.ini* · 10 file(s), +518 −553
  - `builder/build.cs`
  - `desktop/farmengine.cs`
  - `desktop/hardware.cs`
  - `desktop/mainform.cs`
  - `desktop/prioritygovernor.cs`
  - `desktop/win32.cs`
  - `scripts/dofus-net-proxy.go`
  - `scripts/mobile-disguise.js`
  - …and 2 more
- **2026-10-16** `016c0c7` — *Update farmengine.cs* · 1 file(s), +46 −37
  - `desktop/farmengine.cs`
- **2026-10-17** `43c7f58` — *Revert "."* · 13 file(s), +593 −133
  - `builder/Build.exe`
  - `builder/build.cs`
  - `desktop/farmengine.cs`
  - `desktop/mainform.cs`
  - `desktop/prioritygovernor.cs`
  - `desktop/watchdog.cs`
  - `desktop/win32.cs`
  - `f4276a92-d953-4011-998f-0dfccad9285f_DofusFarm.exe`
  - …and 5 more
- **2026-10-17** `c825fe1` — *Reapply "."* · 53 file(s), +402 −2062
  - `compare-cores.ps1`
  - `desktop/farmengine.cs`
  - `desktop/prioritygovernor.cs`
  - `live-test-instance.ps1`
  - `reprovision-all.ps1`
  - `sdk-stripped-backup/emulator/bin64/cygblkid-1.dll`
  - `sdk-stripped-backup/emulator/bin64/cygcom_err-2.dll`
  - `sdk-stripped-backup/emulator/bin64/cyge2p-2.dll`
  - …and 45 more
- **2026-10-17** `afc9544` — *Update farmengine.cs* · 1 file(s), +2 −2
  - `desktop/farmengine.cs`
- **2026-10-17** `fd01150` — *Update episodic_skills.json* · 3 file(s), +87 −10
  - `dofus-network/ai/dofus_native_model.py`
  - `dofus-network/data/cache/episodic_skills.json`
  - `tests/test_dofus_native_ai.py`

### Reworded, not squashed

| date | sha | was | now | files | +/− |
|---|---|---|---|---|---|
| 2026-10-14 | `2c8830d` | *Update farmengine.cs* | **chore(sdk): vendor the Android emulator tree** | 233 | +133806 −0 |
| 2026-10-15 | `10c1518` | *Update mobile-disguise.js* | **chore(sdk): strip and back up the emulator tree** | 198 | +2645 −117137 |
| 2026-10-17 | `6a42a0a` | *last version* | **chore(sdk): update the stripped emulator backup** | 53 | +402 −2062 |
| 2026-10-19 | `157ac9d` | *done* | **feat(client): rework the www client tree** | 212 | +123324 −8324 |
| 2026-10-26 | `da440e8` | *documentation update* | **chore(dofus-network): restructure kaggle, data, ai and combat** | 423 | +430151 −835963 |

Each carries a body line stating the file count, the line delta and the original label,
so the correction is visible in `git log` rather than silent.

---

## Totals across all passes

| pass | before | after | squashed | reworded |
|---|---|---|---|---|
| 1 — no message at all (`.` / `@…`) | 295 | 186 | 109 | — |
| 2 — message names nothing | 190 | 184 | 6 | 5 |
| **net** | **295** | **184** | **115** | **5** |

`.git` went from 910 MB to 178 MB. Almost none of that was commits — it was 54 Cline checkpoint
refs holding a 760 MB `sdk.7z.old-payload` blob. See `MAINTENANCE.md`.

**Recovery: not possible.** The bundles that held Parts 1 and 3 were deleted permanently on
2026-11-07, as were the local backup branch and the `v2.3` tag. No copy of the pre-rewrite history
remains anywhere. This document is the record, not a pointer to one.
