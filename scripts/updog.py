"""Archive Updog by Datadog's outage detections, so history outlives Updog's ~90-day window.

Updog (https://updog.ai) detects degradations of ~30 SaaS providers and AWS services from anonymized
Datadog customer telemetry. It publishes the last ~90 days at UPDOG_URL. Each weekly run:

  1. saves the raw file, gzipped, to data/updog/raw/YYYY-MM-DD.json.gz (provenance; never edited)
  2. merges every outage into data/updog/outages.csv: new ones are added, ongoing ones get their end
     time and status updated, and rows that have scrolled out of Updog's window are kept.

The archive therefore grows week by week from the first run. This data belongs to Datadog; it is not
covered by this repo's CC BY license. See data/updog/README.md.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
from pathlib import Path

UPDOG_URL = "https://updog.ai/data/third-party-outages.json"
FIELDS = [
    "outage_id", "provider", "display_name", "start_utc", "end_utc", "duration_minutes", "status", "kind",
    "impacted_region", "impacted_endpoints", "status_page_incidents", "first_seen", "last_seen",
]


def _ts(ms) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso(d: dt.datetime | None) -> str:
    return d.strftime("%Y-%m-%dT%H:%MZ") if d else ""


def find_providers(obj) -> list[dict]:
    """Every dict with an `outages` list, wherever it sits in the file (tolerates layout changes)."""
    found = []
    if isinstance(obj, dict):
        if isinstance(obj.get("outages"), list):
            found.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found += find_providers(v)
    elif isinstance(obj, list):
        for v in obj:
            found += find_providers(v)
    return found


def _incident_label(inc) -> str:
    if not isinstance(inc, dict):
        return str(inc)[:120]
    title = inc.get("title") or inc.get("name") or inc.get("id") or ""
    url = inc.get("url") or inc.get("shortlink") or inc.get("link") or ""
    return " ".join(x for x in (str(title)[:120], str(url)) if x)


def parse(data) -> list[dict]:
    rows = []
    for p in find_providers(data):
        name = str(p.get("provider_name") or p.get("name") or p.get("display_name") or "unknown")
        display = str(p.get("display_name") or name)
        for o in p.get("outages", []):
            if not isinstance(o, dict):
                continue
            start, end = _ts(o.get("start")), _ts(o.get("end"))
            if not start:
                continue
            region = o.get("impacted_region") or ""
            if isinstance(region, list):
                region = ", ".join(map(str, region))
            endpoints = o.get("impacted_endpoints") or []
            incidents = o.get("linked_status_page_incidents") or []
            status = str(o.get("status") or ("resolved" if end else "ongoing"))
            rows.append({
                "outage_id": f"{name}|{_iso(start)}|{region}",
                "provider": name, "display_name": display,
                "start_utc": _iso(start), "end_utc": _iso(end) if status != "ongoing" else "",
                "duration_minutes": str(round((end - start).total_seconds() / 60)) if end and status != "ongoing" else "",
                "status": status, "kind": str(o.get("kind") or ""), "impacted_region": str(region),
                "impacted_endpoints": " | ".join(map(str, endpoints))[:500] if isinstance(endpoints, list) else str(endpoints)[:500],
                "status_page_incidents": " | ".join(_incident_label(i) for i in incidents)[:500] if isinstance(incidents, list) else "",
            })
    return rows


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["outage_id"]: r for r in csv.DictReader(f)}


def save(path: Path, archive: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(archive.values(), key=lambda r: (r["start_utc"], r["provider"]), reverse=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def merge(archive: dict[str, dict], fresh: list[dict], today: dt.date) -> dict:
    """Add new outages and update changed ones. Returns counts for the report."""
    stats = {"new": 0, "updated": 0, "unchanged": 0}
    for r in fresh:
        old = archive.get(r["outage_id"])
        r = dict(r, last_seen=today.isoformat())
        if old is None:
            archive[r["outage_id"]] = dict(r, first_seen=today.isoformat())
            stats["new"] += 1
            continue
        changed = any(old.get(k, "") != r[k] for k in FIELDS if k not in ("first_seen", "last_seen"))
        archive[r["outage_id"]] = dict(old, **{k: r[k] for k in FIELDS if k != "first_seen" and (r.get(k) or k in ("status", "last_seen"))})
        stats["updated" if changed else "unchanged"] += 1
    return stats


def run(fetch, out_dir: Path, today: dt.date, dry_run: bool = False) -> dict:
    """Fetch, archive, and return a summary dict (always; errors are reported, not raised)."""
    raw = fetch.get(UPDOG_URL)
    summary = {"ok": False, "new": 0, "updated": 0, "fetched": 0, "archive_size": 0, "since": "", "error": "",
               "this_week": []}
    archive_path = out_dir / "outages.csv"
    archive = load(archive_path)
    if not raw:
        summary["error"] = "could not download Updog data"
    else:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            fresh = parse(data)
            if not fresh and not find_providers(data):
                raise ValueError("no providers with an 'outages' list found; Updog's format may have changed")
        except (ValueError, json.JSONDecodeError) as e:
            summary["error"] = f"could not read Updog data: {e}"
            fresh = None
        if not dry_run:
            (out_dir / "raw").mkdir(parents=True, exist_ok=True)
            with gzip.open(out_dir / "raw" / f"{today.isoformat()}.json.gz", "wb", compresslevel=9) as f:
                f.write(raw)
        if fresh is not None:
            stats = merge(archive, fresh, today)
            summary.update(ok=True, fetched=len(fresh), new=stats["new"], updated=stats["updated"])
            if not dry_run:
                save(archive_path, archive)
    summary["archive_size"] = len(archive)
    starts = sorted(r["start_utc"] for r in archive.values() if r["start_utc"])
    summary["since"] = starts[0][:10] if starts else ""
    week_ago = (today - dt.timedelta(days=7)).isoformat()
    summary["this_week"] = [r for r in archive.values() if r["start_utc"][:10] >= week_ago]
    return summary


def matches(archive_rows, provider_terms: list[str], day: dt.date, window_days: int = 1) -> list[dict]:
    """Updog outages whose provider matches any term and that started within window_days of `day`."""
    terms = [t.lower() for t in provider_terms]
    out = []
    for r in archive_rows:
        name = f"{r['provider']} {r['display_name']}".lower()
        if not any(t in name for t in terms) or not r["start_utc"]:
            continue
        start = dt.date.fromisoformat(r["start_utc"][:10])
        if abs((start - day).days) <= window_days:
            out.append(r)
    return out


def report_markdown(summary: dict) -> str:
    L = ["## Updog by Datadog archive (`data/updog/outages.csv`)", ""]
    if summary["error"]:
        L.append(f"**Problem this week:** {summary['error']}. The archive was left unchanged.")
        L.append("")
    if summary["ok"]:
        L.append(f"Updog listed {summary['fetched']} detections; {summary['new']} were new to the archive and "
                 f"{summary['updated']} had changed (e.g. an ongoing one that resolved).")
    L.append(f"The archive holds {summary['archive_size']} detections since {summary['since'] or '—'}.")
    week = summary["this_week"]
    if week:
        by = {}
        for r in week:
            d = by.setdefault(r["display_name"], [0, 0])
            d[0] += 1
            d[1] += int(r["duration_minutes"] or 0)
        top = sorted(by.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:10]
        L += ["", "Most degraded in the past 7 days:", "", "| Provider | Detections | Total minutes |", "|---|---|---|"]
        L += [f"| {name} | {n} | {mins:,} |" for name, (n, mins) in top]
    L.append("")
    return "\n".join(L)
