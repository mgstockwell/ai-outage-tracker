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
            iso = ["--tracking", str(Path(tmp) / "tracking.csv"), "--outages", str(Path(tmp) / "outages.csv"),
                   "--updog-dir", str(Path(tmp) / "updog")]
            import shutil
            shutil.copy(ROOT / "data" / "outages.csv", Path(tmp) / "outages.csv")
            rc = g.main(["--fixtures", str(FIX / "fixtures.json"), "--out", tmp, "--now", "2026-11-17T12:00:00Z"] + iso)
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
            g.main(["--fixtures", str(FIX / "fixtures.json"), "--out", tmp, "--now", "2026-11-17T12:00:00Z"] + iso)
            rows2 = list(csv.DictReader(open(Path(tmp) / "2026-11-17.csv", encoding="utf-8")))
            self.assertEqual(rows2, [])


class WatchList(unittest.TestCase):
    """Three weekly runs: detect, update with follow-up coverage, then store."""

    def test_detect_update_store(self):
        import shutil
        import tracking as t
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            outages = tmp / "outages.csv"
            shutil.copy(ROOT / "data" / "outages.csv", outages)
            before = outages.read_text().count("\n")
            common = ["--out", str(tmp / "cand"), "--tracking", str(tmp / "tracking.csv"), "--outages", str(outages),
                      "--updog-dir", str(tmp / "updog")]

            # Week 1: Verizon (2 outlets) and Cloudflare (status page) go on the watch list.
            # Chase is already in the dataset; ChatGPT has a single source. Neither is added.
            g.main(["--fixtures", str(FIX / "fixtures.json"), "--now", "2026-11-17T12:00:00Z"] + common)
            rows = {r["service"]: r for r in t.load(tmp / "tracking.csv")}
            self.assertEqual(sorted(rows), ["Cloudflare", "Verizon"])
            vz = rows["Verizon"]
            self.assertEqual((vz["status"], vz["incident_date"], vz["store_after"]), ("watching", "2026-11-14", "2026-12-05"))
            self.assertEqual((vz["sources"], vz["max_reports"], vz["cause_type"]), ("2", "180000", ""))
            # Updog saw Cloudflare degraded 04:00-06:30 UTC that day: its measured window becomes the duration.
            cf = rows["Cloudflare"]
            self.assertEqual((cf["duration_hours"], cf["duration_source"]), ("2.5", "Updog"))
            self.assertIn("Cloudflare 11-15 04:00–06:30 UTC", cf["updog_match"])
            self.assertIn("## Updog by Datadog archive", (tmp / "cand" / "latest.md").read_text())

            # Week 2: follow-up coverage adds the cause, duration and another outlet to the same incident.
            g.main(["--fixtures", str(FIX / "fixtures_week2.json"), "--now", "2026-11-24T12:00:00Z"] + common)
            rows = {r["service"]: r for r in t.load(tmp / "tracking.csv")}
            self.assertEqual(sorted(rows), ["Cloudflare", "Verizon"])  # AT&T single-source mention not added
            vz = rows["Verizon"]
            self.assertEqual(vz["incident_date"], "2026-11-14")
            self.assertEqual(vz["cause_type"], "Change / config error")
            self.assertIn("software update", vz["cause_text"])
            self.assertEqual(vz["duration_hours"], "10")
            self.assertEqual(vz["sources"], "3")
            self.assertEqual(vz["last_updated"], "2026-11-24")
            self.assertIn("Updated this week", (tmp / "cand" / "latest.md").read_text())
            # The Updog archive keeps outages that dropped out of Updog's window, and updates ongoing ones.
            import updog as u
            arch = u.load(tmp / "updog" / "outages.csv")
            self.assertEqual(len(arch), 3)
            ddb = [r for r in arch.values() if r["display_name"] == "Amazon DynamoDB" and r["impacted_region"]][0]
            self.assertEqual((ddb["status"], ddb["duration_minutes"], ddb["first_seen"], ddb["last_seen"]),
                             ("resolved", "90", "2026-11-17", "2026-11-24"))
            self.assertEqual(len(list((tmp / "updog" / "raw").glob("*.json.gz"))), 2)

            # A person marks Cloudflare as a false alarm.
            watch = t.load(tmp / "tracking.csv")
            for r in watch:
                if r["service"] == "Cloudflare":
                    r["status"] = "dismissed"
            t.save(tmp / "tracking.csv", watch)

            # Week 4: Verizon's store_after date has passed, so it is stored with what was learned.
            g.main(["--fixtures", str(FIX / "fixtures_empty.json"), "--now", "2026-12-07T12:00:00Z"] + common)
            rows = {r["service"]: r for r in t.load(tmp / "tracking.csv")}
            self.assertEqual(rows["Verizon"]["status"], "stored")
            self.assertEqual(rows["Cloudflare"]["status"], "dismissed")
            new = [r for r in csv.DictReader(outages.open()) if r["date"] == "2026-11-14"]
            self.assertEqual(len(new), 1)
            self.assertEqual((new[0]["service"], new[0]["cause_type"], new[0]["duration_hours"], new[0]["reports"],
                              new[0]["reports_type"], new[0]["era"]),
                             ("Verizon", "Change / config error", "10", "180000", "Peak (news)", "AI era"))
            self.assertEqual(outages.read_text().count("\n"), before + 1)

            # Later coverage of a stored incident is flagged, not turned into a new incident.
            rows_list = t.load(tmp / "tracking.csv")
            later = [{"service": "Verizon", "category": "Cell & ISP", "first": dt.date(2026, 12, 20), "last": dt.date(2026, 12, 20),
                      "sources": 2, "status_page": False, "max_reports": 0, "tracked": False,
                      "rows": [{"title": "FCC report explains what caused Verizon outage", "url": "https://x.example/fcc", "domain": "x.example"}]}]
            rep = t.update(rows_list, later, dt.date(2026, 12, 21), 21)
            self.assertEqual((len(rep["new"]), len(rep["late"])), (0, 1))

            # Running again stores nothing twice.
            g.main(["--fixtures", str(FIX / "fixtures_empty.json"), "--now", "2026-12-14T12:00:00Z"] + common)
            self.assertEqual(outages.read_text().count("\n"), before + 1)

    def test_guessers(self):
        import tracking as t
        self.assertEqual(t.guess_cause("The outage was caused by a configuration change.")[0], "Change / config error")
        self.assertEqual(t.guess_cause("Bank blamed a third-party vendor")[0], "Third-party / vendor")
        self.assertEqual(t.guess_cause("Outage traced to a power failure at a data center")[0], "Hardware / power")
        self.assertIsNone(t.guess_cause("Verizon outage hits millions"))
        self.assertEqual(t.guess_duration("service was down for nearly 10 hours"), 10)
        self.assertEqual(t.guess_duration("a 12-hour-long outage"), 12)
        self.assertIsNone(t.guess_duration("reports in the past day"))


class Updog(unittest.TestCase):
    def test_parse_tolerates_layout(self):
        import updog as u
        rows = u.parse(json.loads((FIX / "updog.json").read_text()))
        self.assertEqual(len(rows), 3)
        ongoing = [r for r in rows if r["status"] == "ongoing"][0]
        self.assertEqual((ongoing["end_utc"], ongoing["duration_minutes"], ongoing["impacted_endpoints"]), ("", "", "GetItem | PutItem"))
        cf = [r for r in rows if r["provider"] == "cloudflare"][0]
        self.assertEqual((cf["start_utc"], cf["duration_minutes"]), ("2026-11-15T04:00Z", "150"))
        self.assertIn("cloudflarestatus.com/incidents/aaa", cf["status_page_incidents"])
        # same data as a bare list, or nested differently, still parses
        self.assertEqual(len(u.parse(json.loads((FIX / "updog.json").read_text())["provider_data"])), 3)
        self.assertEqual(len(u.parse({"data": {"providers": json.loads((FIX / "updog.json").read_text())["provider_data"]}})), 3)

    def test_bad_file_leaves_archive_alone(self):
        import updog as u
        class F:
            def get(self, url):
                return b'{"something": "else"}'
        with tempfile.TemporaryDirectory() as tmp:
            s = u.run(F(), Path(tmp), dt.date(2026, 11, 19))
            self.assertFalse(s["ok"])
            self.assertIn("format may have changed", s["error"])
            self.assertFalse((Path(tmp) / "outages.csv").exists())


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
