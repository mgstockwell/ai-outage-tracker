"""Offline tests: python -m unittest discover tests"""
import csv
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gather_sources as g  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


class Helpers(unittest.TestCase):
    def test_reports_mentioned(self):
        self.assertEqual(g.reports_mentioned("more than 180,000 reports on Downdetector"), 180000)
        self.assertEqual(g.reports_mentioned("over 17 million reports"), 17_000_000)
        self.assertEqual(g.reports_mentioned("35K users reported problems"), 35000)
        self.assertIsNone(g.reports_mentioned("no numbers here"))

    def test_parse_date(self):
        self.assertEqual(g.parse_date("20261114T153000Z"), dt.datetime(2026, 11, 14, 15, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(g.parse_date("Mon, 16 Nov 2026 15:00:00 GMT").day, 16)
        self.assertEqual(g.parse_date("2026-11-15T09:00:00Z").hour, 9)

    def test_matching_is_case_sensitive(self):
        m = g.build_matchers({"Chase": {"category": "Banking", "aliases": ["Chase"]}})
        self.assertTrue(g.match_services("Chase app down", m))
        self.assertFalse(g.match_services("police chase ends", m))

    def test_normalize_url(self):
        self.assertEqual(g.normalize_url("https://www.A.com/x/?utm_source=y&id=2"), "https://a.com/x?id=2")


class EndToEnd(unittest.TestCase):
    def test_run_with_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = g.main(["--fixtures", str(FIX / "fixtures.json"), "--out", tmp, "--now", "2026-11-17T12:00:00Z"])
            self.assertEqual(rc, 0)
            rows = list(csv.DictReader(open(Path(tmp) / "2026-11-17.csv", encoding="utf-8")))
            services = sorted({r["service"] for r in rows})
            # Verizon (2 outlets), Chase (news), ChatGPT (news), Cloudflare (status page)
            self.assertEqual(services, ["Chase", "ChatGPT", "Cloudflare", "Verizon"])
            # filtered: stock story, police chase, maintenance, items older than 7 days
            self.assertFalse(any("shares down" in r["title"] or "maintenance" in r["title"].lower() for r in rows))
            self.assertFalse(any("PayPal" in r["title"] for r in rows))
            # Chase on 2026-11-16 is already in outages.csv
            chase = [r for r in rows if r["service"] == "Chase"][0]
            self.assertEqual(chase["already_tracked"], "yes")
            vz = [r for r in rows if r["service"] == "Verizon"]
            self.assertEqual(len(vz), 2)
            md = (Path(tmp) / "latest.md").read_text()
            self.assertIn("## Likely incidents", md)
            self.assertIn("180,000", md)
            # second run reports nothing new (seen_urls)
            g.main(["--fixtures", str(FIX / "fixtures.json"), "--out", tmp, "--now", "2026-11-17T12:00:00Z"])
            rows2 = list(csv.DictReader(open(Path(tmp) / "2026-11-17.csv", encoding="utf-8")))
            self.assertEqual(rows2, [])


class BuildData(unittest.TestCase):
    def test_check_passes(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_data.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads((ROOT / "data" / "outages.json").read_text())
        self.assertTrue(all(r["era"] in ("Pre-AI", "AI era") for r in data))


class Analysis(unittest.TestCase):
    def _row(self, date, cause, reports=None):
        return {"date": date, "service": "X", "era": "AI era" if date >= "2022-11-30" else "Pre-AI", "cause_type": cause,
                "reports": reports, "reports_type": "Total (Downdetector)" if reports else None}

    def test_rules_follow_the_data(self):
        import build_data as b
        yearly = [{"year": 2021, "service": "A", "reports_millions": 10}, {"year": 2025, "service": "B", "reports_millions": 5}]
        # self-inflicted share jumps from 0% to 100%: headline and titles must say so
        rows = [self._row("2019-01-01", "Hardware / power"), self._row("2024-01-01", "Software bug", 5_000_000)]
        a = b.build_analysis(rows, yearly)
        self.assertEqual(a["headline"], "Companies' own changes and bugs are a growing share of outages.")
        self.assertEqual(a["causes_title"], "The mix of root causes has shifted")
        self.assertEqual(a["yearly_title"], "The biggest outage of each year is getting smaller")
        self.assertEqual(a["largest"]["reports_label"], "5 million")
        self.assertEqual(a["eras"]["AI era"]["self_share"], 100)

    def test_current_dataset_findings(self):
        a = json.loads((ROOT / "data" / "analysis.json").read_text())
        self.assertEqual(a["incidents"], len(json.loads((ROOT / "data" / "outages.json").read_text())))


if __name__ == "__main__":
    unittest.main()
