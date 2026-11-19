#!/usr/bin/env python3
"""Validate data/*.csv, rebuild the JSON the dashboard reads, and rewrite the findings.

    python scripts/build_data.py           # validate, then write data/*.json and update README findings
    python scripts/build_data.py --check   # validate and fail if anything generated is out of date (CI)

The CSV files are the source of truth. Edit them, then run this script. It writes:
  data/outages.json, data/yearly_largest.json   rows for the charts and table
  data/analysis.json                            the findings: numbers plus the sentences shown on the page
  README.md                                     the "What the data shows" section, between analysis markers
Output depends only on the CSVs (no timestamps), so --check is stable.
Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
AI_ERA_START = dt.date(2022, 11, 30)  # ChatGPT public launch

CATEGORIES = {"Banking", "Payments & commerce", "Cell & ISP", "Social & streaming", "Cloud & infrastructure", "AI services"}
CAUSES = {"Change / config error", "Change / migration", "Software bug", "Hardware / power", "Third-party / vendor", "Not disclosed"}
REPORT_TYPES = {"", "Total (Downdetector)", "Peak (news)"}

OUTAGE_COLUMNS = ["date", "service", "category", "region", "duration_hours", "duration_note", "reports", "reports_type",
                  "impact_note", "cause_type", "cause", "era", "source"]
YEARLY_COLUMNS = ["year", "service", "reports_millions", "note", "source"]


def num(v: str):
    v = (v or "").strip()
    if v == "":
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def read(path: Path, columns: list[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames != columns:
            raise SystemExit(f"{path.name}: columns must be exactly {columns}\n  found {r.fieldnames}")
        return list(r)


def build_outages(errors: list[str]) -> list[dict]:
    rows = read(DATA / "outages.csv", OUTAGE_COLUMNS)
    out, keys = [], set()
    for i, r in enumerate(rows, start=2):
        where = f"outages.csv line {i} ({r.get('service')})"
        try:
            d = dt.date.fromisoformat(r["date"])
        except ValueError:
            errors.append(f"{where}: date must be YYYY-MM-DD"); continue
        if (r["date"], r["service"]) in keys:
            errors.append(f"{where}: duplicate date + service")
        keys.add((r["date"], r["service"]))
        if r["category"] not in CATEGORIES:
            errors.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
        if r["cause_type"] not in CAUSES:
            errors.append(f"{where}: cause_type must be one of {sorted(CAUSES)}")
        if r["reports_type"] not in REPORT_TYPES:
            errors.append(f"{where}: reports_type must be one of {sorted(REPORT_TYPES)}")
        if r["reports"].strip() and not r["reports_type"]:
            errors.append(f"{where}: reports needs a reports_type (Total or Peak)")
        if not r["source"].startswith(("http://", "https://")):
            errors.append(f"{where}: source must be a URL")
        expected_era = "AI era" if d >= AI_ERA_START else "Pre-AI"
        if r["era"] != expected_era:
            errors.append(f"{where}: era should be {expected_era!r} for this date")
        try:
            dur, rep = num(r["duration_hours"]), num(r["reports"])
        except ValueError:
            errors.append(f"{where}: duration_hours and reports must be numbers or empty"); continue
        rec = {k: (v if v != "" else None) for k, v in r.items()}
        rec["duration_hours"], rec["reports"] = dur, rep
        out.append(rec)
    out.sort(key=lambda r: (r["date"], r["service"]))
    return out


def build_yearly(errors: list[str]) -> list[dict]:
    out = []
    for i, r in enumerate(read(DATA / "yearly_largest.csv", YEARLY_COLUMNS), start=2):
        try:
            out.append({"year": int(r["year"]), "service": r["service"], "reports_millions": num(r["reports_millions"]),
                        "note": r["note"] or None, "source": r["source"]})
        except ValueError:
            errors.append(f"yearly_largest.csv line {i}: year and reports_millions must be numbers")
    return out


README = ROOT / "README.md"
README_START, README_END = "<!-- analysis:start -->", "<!-- analysis:end -->"
SELF_CAUSES = {"Change / config error", "Change / migration", "Software bug"}
STABLE_POINTS = 10   # a change of this many percentage points or less counts as "about the same"


def pct(part: int, whole: int):
    return round(100 * part / whole) if whole else None


def fmt_reports(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " million"
    return f"{n:,.0f}"


def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def build_analysis(outages: list[dict], yearly: list[dict]) -> dict:
    """Numbers and plain-English findings, all derived from the CSVs by fixed rules."""
    eras = {}
    for era in ("Pre-AI", "AI era"):
        rows = [r for r in outages if r["era"] == era]
        known = [r for r in rows if r["cause_type"] != "Not disclosed"]
        eras[era] = {
            "incidents": len(rows), "disclosed": len(known), "undisclosed": len(rows) - len(known),
            "self_share": pct(sum(r["cause_type"] in SELF_CAUSES for r in known), len(known)),
            "vendor_share": pct(sum(r["cause_type"] == "Third-party / vendor" for r in known), len(known)),
        }
    pre, post = eras["Pre-AI"], eras["AI era"]

    totals = [r for r in outages if r["reports"] and r["reports_type"] == "Total (Downdetector)"]
    big = max(totals, key=lambda r: r["reports"]) if totals else None
    y_pre = [y for y in yearly if y["year"] < AI_ERA_START.year + 1 and y["reports_millions"] is not None]
    y_post = [y for y in yearly if y["year"] >= AI_ERA_START.year + 1 and y["reports_millions"] is not None]
    max_pre = max(y_pre, key=lambda y: y["reports_millions"]) if y_pre else None
    max_post = max(y_post, key=lambda y: y["reports_millions"]) if y_post else None

    dates = sorted(r["date"] for r in outages)
    first, last = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])

    # --- the rules that turn numbers into words ---
    self_diff = (post["self_share"] or 0) - (pre["self_share"] or 0)
    vendor_diff = (post["vendor_share"] or 0) - (pre["vendor_share"] or 0)
    causes_stable = abs(self_diff) <= STABLE_POINTS
    vendors_up = vendor_diff > STABLE_POINTS
    vendors_down = vendor_diff < -STABLE_POINTS
    bigger = bool(max_pre and max_post and max_post["reports_millions"] > 1.2 * max_pre["reports_millions"])
    smaller = bool(max_pre and max_post and max_post["reports_millions"] < 0.8 * max_pre["reports_millions"])

    if causes_stable and (vendors_up or bigger):
        headline = "Not in how often they happen, but in how far they reach."
    elif self_diff > STABLE_POINTS:
        headline = "Companies' own changes and bugs are a growing share of outages."
    elif self_diff < -STABLE_POINTS:
        headline = "Companies' own changes and bugs are a shrinking share of outages."
    else:
        headline = "No clear change since AI took off."

    cause_s = (f"Among outages with a disclosed cause, {pre['self_share']}% before ChatGPT's launch and {post['self_share']}% since "
               f"were a company's own change or software bug: " + ("about the same." if causes_stable else
               ("a clear rise." if self_diff > 0 else "a clear fall.")))
    if vendors_up:
        vendor_s = (f"What's new is concentration: {post['vendor_share']}% of explained outages since the launch came from a shared "
                    f"vendor or provider, up from {pre['vendor_share']}%, so one failure can take down banks, carriers and apps at once.")
    elif vendors_down:
        vendor_s = f"Vendor and shared-provider failures fell from {pre['vendor_share']}% to {post['vendor_share']}% of explained outages."
    else:
        vendor_s = f"Vendor and shared-provider failures are about as common as before ({pre['vendor_share']}% vs {post['vendor_share']}%)."
    summary = (f"This set of {len(outages)} major consumer-facing outages ({first.year} to {last:%b} {last.year}) can't count how "
               f"often outages happen, but it shows what causes them and how far they spread. {cause_s} {vendor_s}")
    if big:
        summary += f" The largest single event is {big['service']} on {fmt_date(big['date'])}, with {fmt_reports(big['reports'])} Downdetector reports."

    if bigger:
        yearly_title = "The biggest outage of each year is getting bigger"
    elif smaller:
        yearly_title = "The biggest outage of each year is getting smaller"
    else:
        yearly_title = "The biggest outage of each year is roughly steady"
    if causes_stable and vendors_up:
        causes_title = "Root causes barely changed, except vendors"
    elif causes_stable:
        causes_title = "Root causes barely changed"
    else:
        causes_title = "The mix of root causes has shifted"

    findings = [cause_s, vendor_s]
    if max_pre and max_post:
        findings.append(f"The biggest yearly outage since 2023, {max_post['service']} in {max_post['year']} with "
                        f"{fmt_reports(max_post['reports_millions'] * 1_000_000)} reports, is "
                        + ("bigger than" if bigger else "smaller than" if smaller else "about the same as")
                        + f" the biggest before it, {max_pre['service']} in {max_pre['year']} with {fmt_reports(max_pre['reports_millions'] * 1_000_000)}. "
                        "Downdetector's growing audience accounts for part of any rise.")
    return {
        "data_through": last.isoformat(), "data_through_label": f"{last:%B} {last.day}, {last.year}",
        "incidents": len(outages), "first_year": first.year,
        "eras": eras,
        "largest": ({"service": big["service"], "date": big["date"], "date_label": fmt_date(big["date"]),
                     "reports": big["reports"], "reports_label": fmt_reports(big["reports"])} if big else None),
        "headline": headline, "summary": summary, "findings": findings,
        "yearly_title": yearly_title, "causes_title": causes_title,
    }


def readme_section(a: dict) -> str:
    lines = [README_START, "", f"_Generated by `scripts/build_data.py` from {a['incidents']} outages, data through {a['data_through_label']}._", "",
             f"**{a['headline']}**", ""]
    lines += [f"- {f}" for f in a["findings"]]
    lines += ["- Uptime Institute's [2026 Annual Outage Analysis](https://intelligence.uptimeinstitute.com/resource/annual-outage-analysis-2026) "
              "finds per-site outage frequency has been falling, which this dataset can't measure itself.", "", README_END]
    return "\n".join(lines)


def updated_readme(a: dict) -> str | None:
    if not README.exists():
        return None
    text = README.read_text(encoding="utf-8")
    if README_START not in text or README_END not in text:
        return None
    head, rest = text.split(README_START, 1)
    tail = rest.split(README_END, 1)[1]
    return head + readme_section(a) + tail


def dump(rows) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=1) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if data/*.json differs from what the CSVs produce")
    args = ap.parse_args(argv)
    errors: list[str] = []
    outages, yearly = build_outages(errors), build_yearly(errors)
    if errors:
        print("Data problems:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    analysis = build_analysis(outages, yearly)
    targets = {DATA / "outages.json": dump(outages), DATA / "yearly_largest.json": dump(yearly),
               DATA / "analysis.json": json.dumps(analysis, ensure_ascii=False, indent=1) + "\n"}
    readme = updated_readme(analysis)
    if readme is not None:
        targets[README] = readme
    stale = [p for p, text in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    names = ", ".join(p.relative_to(ROOT).as_posix() for p in stale)
    if args.check:
        if stale:
            print(f"Out of date: {names}. Run: python scripts/build_data.py", file=sys.stderr)
            return 1
        print("Data OK.")
        return 0
    for p in stale:
        p.write_text(targets[p], encoding="utf-8")
    print(f"Data OK. {'Updated ' + names if stale else 'Everything already up to date'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
