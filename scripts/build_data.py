#!/usr/bin/env python3
"""Validate data/*.csv and rebuild the JSON files the dashboard reads.

    python scripts/build_data.py           # validate, then write data/*.json
    python scripts/build_data.py --check   # validate and fail if the JSON is out of date (CI)

The CSV files are the source of truth. Edit them, then run this script.
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


def dump(rows) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=1) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if data/*.json differs from what the CSVs produce")
    args = ap.parse_args(argv)
    errors: list[str] = []
    outputs = {"outages.json": dump(build_outages(errors)), "yearly_largest.json": dump(build_yearly(errors))}
    if errors:
        print("Data problems:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    stale = [n for n, text in outputs.items() if not (DATA / n).exists() or (DATA / n).read_text(encoding="utf-8") != text]
    if args.check:
        if stale:
            print(f"Out of date: {', '.join(stale)}. Run: python scripts/build_data.py", file=sys.stderr)
            return 1
        print("Data OK.")
        return 0
    for n in stale:
        (DATA / n).write_text(outputs[n], encoding="utf-8")
    print(f"Data OK. {'Updated ' + ', '.join(stale) if stale else 'JSON already up to date'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
