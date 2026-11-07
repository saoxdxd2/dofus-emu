"""Deterministic tests for the play-log projection: no corpus, no network.

The corpus is represented by a handful of literal records, one per `kind`, so a
change in the recorder's wire shape shows up here as a failing expectation
rather than as a quietly emptier output file.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from . import market_log
except ImportError:  # Also support: python tools/test_market_log.py
    import market_log


PLAY = [
    {"type": "PLAY_LOG", "session": "s1", "seq": 0, "at": 1792998748508,
     "event": {"kind": "session_start", "label": "auto",
               "href": "https://dofus.local/standalone.html?buildVersion=1.74.9"}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 1, "at": 1792998748600,
     "event": {"kind": "map_change", "from": 0, "to": 147062786, "cell": None}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 2, "at": 1792998748700,
     "event": {"kind": "send", "via": "sendMessage",
               "name": "ExchangeBidHouseBuyMessage", "args": {"gid": 18844}}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 3, "at": 1792998748800,
     "event": {"kind": "recv", "name": "ObjectAveragePricesMessage",
               "args": {"_truncated": True, "length": 55488,
                        "preview": '{"_messageType":"ObjectAveragePricesMessage","ids":[1,2]'}}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 4, "at": 1792998748900,
     "event": {"kind": "fight_start",
               "state": {"map": 5, "hp": 55, "max": 55, "xp": 0, "level": 1,
                         "kamas": 0, "fight": True, "inv": 1}}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 5, "at": 1792998749000,
     "event": {"kind": "state_delta", "diff": {"kamas": {"from": 0, "to": 1200}},
               "state": {"map": 5, "kamas": 1200, "fight": False}}},
    {"type": "PLAY_LOG", "session": "s1", "seq": 6, "at": 1792998749100,
     "event": {"kind": "console", "level": "log", "text": "[Recorder] noise"}},
]

MARKET = {
    "captured_at": 1792998800000,
    "average_at": 1792998790000,
    "average_age_ms": 10000,
    "prices": [[18844, 1200], [18974, 900]],
    "listings": {"18844": {"count": 3, "min": 900, "median": 1000, "max": 1300,
                           "age_ms": 5000}},
}


def write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class ClassifyTests(unittest.TestCase):
    def test_market_messages_are_market(self):
        for name in ("ExchangeBidHouseBuyMessage", "ObjectAveragePricesMessage",
                     "setShopDetailsRequest", "ExchangeObjectMovePricedMessage"):
            with self.subTest(name=name):
                self.assertEqual(market_log.classify(name), "market")

    def test_object_is_not_market_when_it_is_only_an_object(self):
        # `Object` is an inventory prefix and `ObjectAveragePrices` is a market
        # one; order in VERB_RULES decides, so this pins the order.
        self.assertEqual(market_log.classify("ObjectAddedMessage"), "inventory")

    def test_known_buckets(self):
        cases = {
            "ChangeMapMessage": "movement",
            "GameFightTurnStartMessage": "combat",
            "NpcGenericActionRequestMessage": "dialog",
            "ChatMessage": "social",
            "BasicPingMessage": "session",
            "SomethingElseMessage": "other",
            "": "other",
            None: "other",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(market_log.classify(name), want)


class ProjectActionTests(unittest.TestCase):
    def test_console_is_dropped(self):
        self.assertIsNone(market_log.project_action("p.jsonl", PLAY[6]))

    def test_a_send_row_carries_direction_and_verb(self):
        row = market_log.project_action("p.jsonl", PLAY[2])
        self.assertEqual(row["record"], "action")
        self.assertEqual(row["dir"], "out")
        self.assertEqual(row["verb"], "market")
        self.assertEqual(row["msg"], "ExchangeBidHouseBuyMessage")
        self.assertEqual(row["seq"], 2)
        self.assertEqual(row["session"], "s1")
        self.assertEqual(row["at_iso"], "2026-10-26T07:12:28.700000+00:00")

    def test_recv_is_inbound(self):
        row = market_log.project_action("p.jsonl", PLAY[3])
        self.assertEqual(row["dir"], "in")
        self.assertEqual(row["verb"], "market")

    def test_map_change_becomes_a_move_row(self):
        row = market_log.project_action("p.jsonl", PLAY[1])
        self.assertEqual((row["verb"], row["msg"]), ("move", "map_change"))
        self.assertEqual(row["map"], 147062786)
        self.assertEqual(row["map_from"], 0)
        self.assertNotIn("cell", row, "a null cell must not be written")

    def test_fight_edges_keep_the_state_they_were_sent_with(self):
        row = market_log.project_action("p.jsonl", PLAY[4])
        self.assertEqual(row["verb"], "combat")
        self.assertTrue(row["fight"])
        self.assertEqual(row["hp"], 55)
        self.assertEqual(row["max"], 55)

    def test_state_delta_keeps_only_the_destination(self):
        row = market_log.project_action("p.jsonl", PLAY[5])
        self.assertEqual(row["delta"], {"kamas": 1200})
        self.assertEqual(row["kamas"], 1200)

    def test_session_start_and_connect_are_kept(self):
        start = market_log.project_action("p.jsonl", PLAY[0])
        self.assertEqual(start["verb"], "session")
        self.assertIn("1.74.9", start["href"])
        connect = market_log.project_action("p.jsonl", {
            "session": "s1", "seq": 7, "at": 1,
            "event": {"kind": "connect", "args": [{"groups": False}, "https://x.example"]}})
        self.assertEqual(connect["target"], "https://x.example")

    def test_input_rows_survive(self):
        click = market_log.project_action("p.jsonl", {
            "session": "s1", "seq": 8, "at": 1,
            "event": {"kind": "click", "tag": "div", "id": "resizableBody",
                      "cls": "", "x": 1, "y": 2, "cell": None}})
        self.assertEqual((click["verb"], click["msg"]), ("input", "click"))
        self.assertEqual(click["tag"], "div")
        key = market_log.project_action("p.jsonl", {
            "session": "s1", "seq": 9, "at": 1,
            "event": {"kind": "key", "key": "Alt", "code": "AltLeft", "typed": False}})
        self.assertEqual(key["key"], "Alt")


class ProjectCaptureTests(unittest.TestCase):
    def test_one_row_per_gid_with_its_listing(self):
        rows = market_log.project_capture("market.json", MARKET)
        self.assertEqual([row["gid"] for row in rows], [18844, 18974])
        first = rows[0]
        self.assertEqual(first["record"], "market")
        self.assertEqual(first["average"], 1200)
        self.assertEqual(first["average_at"], 1792998790000)
        self.assertEqual(first["listing"]["min"], 900)
        self.assertNotIn("listing", rows[1], "a gid with no listing gets no listing key")

    def test_a_bridge_envelope_is_unwrapped(self):
        envelope = {"ok": True, "result": {"data": MARKET}}
        self.assertEqual(len(market_log.project_capture("m.json", envelope)), 2)

    def test_bad_entries_are_skipped_not_fatal(self):
        doc = {"captured_at": 1, "prices": [[18844, 1200], ["x", 5], [0, 5],
                                            [18974, 0], None, [1]]}
        rows = market_log.project_capture("m.json", doc)
        self.assertEqual([row["gid"] for row in rows], [18844])

    def test_newest_capture_wins(self):
        old = {"captured_at": 100, "average_at": 100, "prices": [[18844, 1000]]}
        new = {"captured_at": 200, "average_at": 200, "prices": [[18844, 1500]]}
        rows = market_log.merge_market_rows(
            market_log.project_capture("a.json", old)
            + market_log.project_capture("b.json", new))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["average"], 1500)

    def test_a_reread_of_an_old_table_does_not_look_new(self):
        # Same average_at, later captured_at: the table did not move, so the
        # earlier row must not win just because it was read last.
        stale = {"captured_at": 999, "average_at": 100, "prices": [[18844, 1000]]}
        fresh = {"captured_at": 200, "average_at": 200, "prices": [[18844, 1500]]}
        rows = market_log.merge_market_rows(
            market_log.project_capture("a.json", fresh)
            + market_log.project_capture("b.json", stale))
        self.assertEqual(rows[0]["average"], 1500)


class MarketFrameScanTests(unittest.TestCase):
    """The check must be able to say yes as well as no."""

    def scan(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "play_x.jsonl"
            write_jsonl(path, rows)
            return market_log.scan_market_frames([str(path)])

    def test_a_truncated_frame_yields_no_prices(self):
        stats = self.scan([PLAY[3]])
        self.assertEqual(stats["frames"], 1)
        self.assertEqual(stats["truncated"], 1)
        self.assertEqual(stats["recoverable_prices"], 0)

    def test_an_intact_frame_yields_its_prices(self):
        rows = [{"type": "PLAY_LOG", "session": "s", "seq": 0, "at": 1,
                 "event": {"kind": "recv", "name": "ObjectAveragePricesMessage",
                           "args": {"ids": [1, 2], "avgPrices": [10, 20]}}}]
        stats = self.scan(rows)
        self.assertEqual(stats["truncated"], 0)
        self.assertEqual(stats["recoverable_prices"], 2)

    def test_a_preview_that_kept_the_prices_is_counted(self):
        rows = [{"type": "PLAY_LOG", "session": "s", "seq": 0, "at": 1,
                 "event": {"kind": "recv", "name": "ObjectAveragePricesMessage",
                           "args": {"_truncated": True, "length": 9000,
                                    "preview": '{"ids":[1],"avgPrices":[10]'}}}]
        self.assertEqual(self.scan(rows)["previews_with_prices"], 1)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.play = self.dir / "play_dofus-04.jsonl"
        write_jsonl(self.play, PLAY)
        self.market = self.dir / "market.json"
        self.market.write_text(json.dumps(MARKET), encoding="utf-8")
        self.out = self.dir / "normalized.jsonl"

    def run_main(self, argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = market_log.main(argv)
        return code, stdout.getvalue()

    def test_both_record_types_are_written_in_order(self):
        code, _ = self.run_main([str(self.play), "--out", str(self.out),
                                 "--captures", str(self.market)])
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in
                self.out.read_text(encoding="utf-8").splitlines()]
        kinds = [row["record"] for row in rows]
        self.assertEqual(kinds.count("action"), 6, "the console row must be dropped")
        self.assertEqual(kinds.count("market"), 2)
        self.assertEqual(kinds[0], "action", "actions come first, in file order")
        self.assertEqual(kinds[-1], "market")
        self.assertEqual([row["seq"] for row in rows if row["record"] == "action"],
                         [0, 1, 2, 3, 4, 5])

    def test_only_market_skips_the_play_log(self):
        code, _ = self.run_main([str(self.play), "--out", str(self.out),
                                 "--captures", str(self.market), "--only", "market"])
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in
                self.out.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(row["record"] == "market" for row in rows))
        self.assertEqual(len(rows), 2)

    def test_check_reports_that_play_logs_hold_no_prices(self):
        code, out = self.run_main([str(self.play), "--check", "--json",
                                   "--captures", str(self.market)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["prices_from_play_logs"], 0)
        self.assertEqual(report["market_rows"], 2)
        self.assertEqual(report["records"], 7)
        self.assertEqual(report["actions"], 6)

    def test_require_market_fails_when_no_capture_exists(self):
        code, _ = self.run_main([str(self.play), "--out", str(self.out),
                                 "--captures", str(self.dir / "absent.json"),
                                 "--only", "market", "--require-market"])
        self.assertEqual(code, 1)

    def test_limit_stops_the_stream(self):
        code, _ = self.run_main([str(self.play), "--out", str(self.out),
                                 "--captures", str(self.market), "--limit", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.out.read_text(encoding="utf-8").splitlines()), 2)

    def test_stdout_mode_writes_nothing_to_disk(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = market_log.main([str(self.play), "--out", "-",
                                    "--captures", str(self.market), "--only", "market"])
        self.assertEqual(code, 0)
        self.assertEqual(len(stdout.getvalue().strip().splitlines()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
