# Archive — what was removed, and why

This file is the only record of files deleted in the 2026-10-23 cleanup. Every entry
is `path — what it did — why removed`. Everything here is tracked by git, so
`git show <sha>:<path>` restores it. Two clusters dominate: the THX-01 decoder
evaluation sets, and the Osamodas spell-data excavation of 18–19 October.

The evidence for each removal was a repository-wide grep for the filename; for every
file below the only hits were inside files removed in the same pass, or none at all.
Nothing in `desktop/aipanel.cs` names any of these — the app launches exactly
`dofus-network/run_autopilot.py` and `dofus-network/ai/idle_trainer.py`.

## dofus-network/ai/

- `dofus-network/ai/ab_scores.py` — A/B'd the two simulator fork scores (rate vs cummean) for label ambiguity and quests completed — the experiment is spent: its verdict is in `now.md` §3 (34.5% vs 27.7% ambiguity, 98.6 vs 99.6 quests, cummean rejected), the switch it measured still lives in `sim/tool_choice.py --score`, and the corpora it read (`data/ab_rate`, `data/ab_cummean`) no longer exist, so it cannot run.
- `dofus-network/ai/cost_probe.py` — measured decoder-student cost (RSS, latency, prompt tokens) for the THX-01 100M checkpoint — superseded by `encoder_cost_probe.py` (the encoder line, invoked by `colab/kaggle_eval_v10.py`) and `measure_rss.py` (the artifacts the app actually loads); nothing imports it and no doc cites it.
- `dofus-network/ai/thx_test.py` — first fresh held-out phrasing set for the THX-01 policy, written to expose the argument-parsing bugs — superseded once those bugs were fixed and `thx_test2.py` was written to test the fixes; its only reference was `thx_test2.py`, also removed here.
- `dofus-network/ai/thx_test2.py` — second held-out set, written after the arg fixes — superseded the same way by `thx_test3.py`; its only reference was `thx_test3.py`, also removed here.
- `dofus-network/ai/thx_test3.py` — third held-out set, stressing new synonyms and sentence shapes — superseded by `thx_test4.py`, which is kept as the current blind set for `thx_policy`; nothing referenced this file.

## scripts/ — the Osamodas spell-data excavation (18–19 Oct)

The chain was: scrape dofus-touch/Wayback HTML → pull spell IDs out of it → match them
by name against `data/cache/spells_full.json` → assemble `scripts/output/osa_spells.json`.
The result is kept: `scripts/output/osa_spells.json` (10 KB) and
`scripts/output/all_spells.json` (452 KB) are still there, and `spells_full.json` is
unchanged. Only the probes that produced them are gone.

- `scripts/analyze_osa.py` — printed stat counts over `spells_full.json` while hunting the Osamadas spells — one-off probe, zero references.
- `scripts/analyze_spells.py` — counted field and type distributions in `spells_full.json` — one-off probe; its only "reference" is its own captured output, `scripts/explore_out.txt`.
- `scripts/check_combat_data.py` — cross-checked combat spell names against `spells_full.json` — one-off probe, zero references.
- `scripts/check_dofus_touch_ids.py` — checked the ID range of the dofus-touch spells added to `spells_full.json` — one-off probe, zero references.
- `scripts/check_duplicates.py` — looked for duplicate spell entries in `spells_full.json` — one-off probe, zero references.
- `scripts/check_extra.py` — counted the entries in `data/cache/spells_extra.json` — one-off probe; the file it counted was written by `sync_more_spells2.py`, also removed here.
- `scripts/check_id_ranges.py` — printed the ID gaps in `spells_full.json` — one-off probe, zero references.
- `scripts/check_max_id.py` — printed the highest spell ID in `spells_full.json` — one-off probe, zero references.
- `scripts/check_messages.py` — grepped the game bundle `desktop/client/www/build/script.js` for protocol message names — one-off probe, zero references.
- `scripts/check_nearby_ids.py` — listed spells whose IDs neighbour the Osamadas range — one-off probe, zero references.
- `scripts/check_spell_schema.py` — printed the keys of the first few `spells_full.json` records — one-off probe, zero references.
- `scripts/check_summons.py` — listed spells carrying a `summon` field in `osa_spells.json` — one-off probe, zero references.
- `scripts/complete_osa_spells.py` — merged the name-matched and dofus-touch-only Osamadas spells into `osa_spells.json` — superseded by `rewrite_complete2.py`, which rewrote its source; the finished JSON is kept.
- `scripts/comprehensive_osa_search.py` — brute-force French keyword search for Osamadas spells across `spells_full.json` — one-off probe, zero references.
- `scripts/count_type425.py` — counted the spells of `type_id` 425 — one-off probe, zero references.
- `scripts/dex_search2.py` — regex-searched the APK's `classes.dex` / `classes2.dex` for Osamadas strings — one-off probe, zero references.
- `scripts/dex_strings.py` — dumped dex sizes and Osamadas string hits from the APK — one-off probe, zero references.
- `scripts/explore_apk.py` — printed the repo's xapk and `assets/` layout before extracting — one-off exploration, zero references.
- `scripts/extract_osa_spells_from_corpus.py` — pulled Osamadas spells out of the captured corpus — one-off probe, zero references.
- `scripts/extract_spell_urls.py` — regexed spell links out of the saved dofus-touch HTML — one-off scrape step, zero references.
- `scripts/extract_touch_spells.py` — regexed `spell-id` attributes out of the saved Wayback HTML — one-off scrape step, zero references.
- `scripts/fetch_dofus_osa_raw.py` — fetched the dofus-touch Osamadas page from Wayback — one-off scrape; the HTML it saved is still in `scripts/`.
- `scripts/fetch_osa_spell_detail.py` — fetched individual spell detail pages through Wayback — one-off scrape, zero references.
- `scripts/fetch_wayback_raw.py` — downloaded the Osamadas encyclopedia page from Wayback — one-off scrape, superseded by `fetch_wayback_raw2.py`; its HTML is still in `scripts/`.
- `scripts/fetch_wayback_raw2.py` — second Wayback attempt over four timestamps — one-off scrape, zero references.
- `scripts/final_osa_list.py` — hard-coded the curated 22 Osamadas spells — its content is in `scripts/output/osa_spells.json`, which is kept.
- `scripts/find_breed_files.py` — `os.walk` for any filename containing "breed" or "class" — one-off filesystem listing, zero references.
- `scripts/find_osa.py` — grepped `data/game_data_extractor.py` for Osamadas mentions — one-off probe, zero references.
- `scripts/find_osa_patterns.py` — searched `spells_full.json` for French Osamadas name patterns — one-off probe, zero references.
- `scripts/fix_metadata.py` — patched metadata fields in `osa_spells.json` — one-off edit, superseded by `update_metadata.py`, also removed here.
- `scripts/fix_summons.py` — patched the summon flags in `osa_spells.json` — one-off edit, zero references.
- `scripts/fuzzy_match_osa.py` — fuzzy-matched dofus-touch names to `spells_full.json` entries — one-off probe, zero references.
- `scripts/generate_osa_json.py` — generated the first `osa_spells.json` from `spells_full.json` — superseded by `complete_osa_spells.py` and `rewrite_complete2.py`; the JSON is kept.
- `scripts/list_assets.py` — listed the asset entries inside the extracted APK — one-off listing, zero references.
- `scripts/list_data.py` — printed a file listing of `dofus-network/data` — one-off listing, zero references.
- `scripts/list_dofus_net.py` — printed a file listing of `dofus-network` — one-off listing, zero references.
- `scripts/list_type425.py` — listed the `type_id` 425 spells — one-off probe, zero references.
- `scripts/match_fr_names.py` — matched French spell names against the Osamadas list — one-off probe, zero references.
- `scripts/match_osa_final.py` — the final name-matching pass for the Osamadas list — one-off probe, zero references.
- `scripts/match_osa_names.py` — matched EN/FR Osamadas names to spell records — one-off probe, zero references.
- `scripts/match_osa_names_fr.py` — French-only variant of `match_osa_names.py` — one-off probe, zero references.
- `scripts/read_complete.py` — printed the first 1000 characters of `complete_osa_spells.py`'s source — a debug print of another script, both removed.
- `scripts/read_js.py` — printed `assets/www/index.html` out of the APK — one-off inspection, zero references.
- `scripts/rewrite_complete.py` — rewrote `complete_osa_spells.py`'s source in place — a script that edited another script's source; both are gone.
- `scripts/rewrite_complete2.py` — second in-place rewrite of `complete_osa_spells.py` — same, and it superseded the first attempt.
- `scripts/search_artifacts.py` — scanned `networkhealth_artifacts/*.jsonl` for Osamadas/breed terms — one-off probe, zero references.
- `scripts/search_breed.py` — scanned `dofus-network/**/*.json` for breed/Osamadas terms — one-off probe, zero references.
- `scripts/search_exact_fr.py` — exact French name search in `spells_full.json` — one-off probe, zero references.
- `scripts/search_extra_osa.py` — searched `spells_extra.json` for Osamadas spells — one-off probe, zero references.
- `scripts/search_osa_fr_broad.py` — broad French keyword search in `spells_full.json` — one-off probe, zero references.
- `scripts/search_osa_keywords.py` — keyword search in `spells_full.json` — one-off probe, zero references.
- `scripts/search_pending.py` — listed the Osamadas spells still missing an ID — one-off probe, zero references.
- `scripts/search_pending2.py` — second pass over the spells still missing an ID — one-off probe, zero references.
- `scripts/sync_more_spells.py` — paginated the dofusdb API for high-ID spells and wrote them into `data/cache/spells_full.json` — one-off pagination probe; the cache is still dated 17 Oct, so its write never landed, and nothing references it.
- `scripts/sync_more_spells2.py` — the same pagination probe, writing to `spells_extra.json` instead — its only readers (`check_extra.py`, `search_extra_osa.py`) are removed in this pass.
- `scripts/update_metadata.py` — rewrote the metadata block in `osa_spells.json` — one-off edit, zero references.
- `scripts/validate_json.py` — re-parsed `osa_spells.json` and `all_spells.json` to prove they load — one-off check; both files are valid and kept.
- `scripts/xapk_inspect.py` — listed the xapk's inner apk/obb entries — one-off inspection, zero references.

## scripts/ — other

- `scripts/bench_navigation.py` — micro-benchmark of the `navigation/` hot paths — it imports `navigation.world_graph`, which no longer exists, so it cannot run; nothing references it.
- `scripts/add_dofus_touch_only.py` — appended the 12 dofus-touch-only Osamadas spells to `osa_spells.json` — its three callers (`complete_osa_spells.py`, `rewrite_complete.py`, `rewrite_complete2.py`) are removed in this pass and the finished JSON is kept.

## repo root

- `setup.exe.pre-snapshot.bak` (67 MB) — hand-rolled copy of the installer taken before the snapshot work — a duplicate of a build output.
- `DofusFarm.exe.pre-recorder.bak` — hand-rolled copy of the app binary from before the recorder work — a duplicate of a build output.
- `DofusFarm.exe.pre-snapshot.bak` — same, from before the snapshot work — a duplicate of a build output.
- `proxies.txt.bak` — previous version of the 408-byte proxy list — superseded by `proxies.txt`.

Note: these four are matched by `.gitignore:*.bak`, so unlike everything else in this
file they are **not** recoverable with `git show`. They were deleted because they are
copies of artefacts that are themselves regenerated by the build, not because git held
a copy.

## Kept deliberately

- `dofus-network/ai/student_bench.py`, `student_hybrid_bench.py` — unreferenced by code, but `docs/AI_ANALYSIS.md:192` cites the hybrid bench when it argues the 100% hybrid score was circular, and `student_bench.py` produces the student report that hybrid bench reads. Removing either breaks that citation.
- `dofus-network/ai/thx_holdout.py`, `thx_test4.py` — the dev set and the current blind set for `thx_policy`; together they are the only evaluation data for a policy `agent/serve.py` still loads.
- `dofus-network/ai/build_sequence_dataset.py`, `export_kaggle_dataset.py`, `train_value.py`, `probe_client.py` — zero importers, but all four are live: the first two are the exporters named in the plan for the sequence corpus, `train_value.py` is explicitly kept by `now.md` ("Trainer and corpus stay; the artifact is 4 minutes to rebuild"), and `probe_client.py` is the CLI written on 23 Oct to debug the null-field reader.
- `scripts/fetch_maps.py` — regenerates `dofus-network/data/maps`, which the KB reads; it is the only record of how that data was crawled.
- `scripts/extract_apk.py` — the only record of how the protected `scripts/apk_extract/` tree was produced from the xapk.
- `dofus-network/colab/` — nothing removed. Each file has a distinct job: `kaggle_train_encoder30.py` is copied into the kernel by `tools/kaggle_continue.py:108`, `kaggle_train_encoder30.ipynb` is cited by `docs/AI_ANALYSIS.md:278`, `train_gpu.py` and `train_networkhealth_t4.ipynb` are documented in `dofus-network/README.md`, and `kaggle_eval_v10.py` scores a finished v10 while the training kernel only trains. `generate_domain_distillation_notebook.py` writes to `notebooks/`, a directory that no longer exists, so it is stale — but it is the only generator of `Fine_Tune_Dofus_THX01.ipynb`, which is kept, so it was left alone rather than guessed at.
