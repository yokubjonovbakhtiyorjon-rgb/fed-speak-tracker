"""Offline tests: python -m unittest discover -s tests -v"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
ROSTER = build.Roster(json.loads((ROOT / "config" / "roster.json").read_text()))
RULES = {k: re.compile(v, re.I) for k, v in json.loads((ROOT / "config" / "topics.json").read_text())["topics"].items()}
SOURCES = {s["id"]: s for s in json.loads((ROOT / "config" / "sources.json").read_text())["sources"]}


def run_adapter(source_id, **extra):
    src = dict(SOURCES[source_id], **extra)
    raw = next(p for p in (FIX / f"{source_id}.xml", FIX / f"{source_id}.html") if p.exists()).read_bytes()
    if src["adapter"] == "html_links":
        return build.adapt_html_links(raw, src, ROSTER, RULES, "2026-10-06")
    return build.ADAPTERS[src["adapter"]](raw, src, ROSTER, RULES)


class BoardFeed(unittest.TestCase):
    def setUp(self):
        self.recs = {r["url"].rsplit("/", 1)[-1]: r for r in run_adapter("board")}

    def test_bom_and_surname_title_split(self):
        r = self.recs["jefferson20260716a.htm"]
        self.assertEqual(r["speaker"], "Philip N. Jefferson")
        self.assertTrue(r["title"].startswith("Navigating Economic Shocks"))
        self.assertTrue(r["voter"])

    def test_gmt_evening_converts_to_eastern_date(self):
        # 23:00 GMT on Jul 16 is 7:00 p.m. EDT on Jul 16.
        self.assertEqual(self.recs["jefferson20260716a.htm"]["date"], "2026-07-16")
        # 00:30 GMT on Jan 12 is 7:30 p.m. EST on Jan 11.
        self.assertEqual(self.recs["powell20260111a.htm"]["date"], "2026-01-11")

    def test_types(self):
        self.assertEqual(self.recs["powell20260111a.htm"]["type"], "Statement")
        self.assertEqual(self.recs["guynn20260326a.htm"]["type"], "Testimony")
        self.assertEqual(self.recs["guynn20260326a.htm"]["tier"], "official")

    def test_entity_decoding(self):
        self.assertIn("Reykjavík", self.recs["bowman20260529a.htm"]["venue"])


class FedInPrint(unittest.TestCase):
    def test_keeps_only_speech_series_and_resolves_staff(self):
        recs = run_adapter("fip_newyork")
        titles = {r["title"] for r in recs}
        self.assertNotIn("Treasury Trading at the Close", titles)          # blog post filtered out
        self.assertNotIn("Consumption, Savings, and Earnings Responses to Financial Windfalls", titles)
        by_title = {r["title"]: r for r in recs}
        self.assertEqual(by_title["Unwavering Dedication"]["speaker"], "John C. Williams")
        self.assertEqual(by_title["Supplying Ample Reserves"]["tier"], "official")
        self.assertEqual(by_title["Economic Conditions in New York State"]["speaker"], "Jaison R. Abel")


class Merge(unittest.TestCase):
    def test_primary_source_wins_and_secondary_becomes_alternate(self):
        fresh = run_adapter("fip_dallas") + run_adapter("dallas_direct")
        merged = build.merge([], fresh, "2026-10-06T12:00:00+00:00")
        self.assertEqual(len(merged), 1)
        self.assertIn("dallasfed.org", merged[0]["url"])
        self.assertEqual(merged[0]["alt_links"][0]["source"], "Fed in Print: Dallas")

    def test_rerun_is_idempotent(self):
        fresh = run_adapter("board")
        once = build.merge([], fresh, "t1")
        twice = build.merge(json.loads(json.dumps(once)), run_adapter("board"), "t2")
        self.assertEqual(len(once), len(twice))


class HtmlListings(unittest.TestCase):
    def test_dates_from_url_and_best_anchor_text(self):
        recs = {r["date"]: r for r in run_adapter("chicago_direct")}
        self.assertEqual(recs["2026-02-19"]["title"], "2026 Joint Conference on Financial Crises")
        self.assertEqual(recs["2026-03-05"]["speaker"], "Austan D. Goolsbee")

    def test_baseline_run_leaves_undated_links_undated(self):
        recs = run_adapter("kansascity_direct", _baseline=True)
        self.assertEqual(len(recs), 2)                       # index.cfm and non-speech links excluded
        self.assertTrue(all(r["date"] is None and r["date_estimated"] for r in recs))

    def test_later_run_stamps_first_seen_date(self):
        recs = run_adapter("kansascity_direct", _baseline=False)
        self.assertTrue(all(r["date"] == "2026-10-06" for r in recs))


class FeedsWithoutTitles(unittest.TestCase):
    def test_boston_title_from_slug_and_default_speaker(self):
        recs = run_adapter("boston_direct")
        self.assertEqual(recs[1]["title"], "Importance Patient Methodical Holistic Approach Monetary Policy")
        self.assertEqual(recs[1]["speaker"], "Susan M. Collins")


if __name__ == "__main__":
    unittest.main()
