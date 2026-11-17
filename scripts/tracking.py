"""Watch list for new outages: detect, update weekly as more is known, then store.

Lifecycle of a row in data/tracking.csv (column `status`):

  watching   found by the weekly scan; each week new coverage updates its sources, report count,
             duration, cause and post-mortem link
  store      set this by hand to store it at the next run, before its store_after date
  stored     copied into data/outages.csv (happens automatically once store_after has passed)
  dismissed  set this by hand for false alarms; the scan then ignores it for good

You can edit any value in a `watching` row (fix a cause, extend store_after, add notes).
The scan only fills a field when it has new information, and never blanks one you set.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

TRACKING_FIELDS = [
    "id", "service", "category", "incident_date", "status", "store_after", "sources", "max_reports",
    "duration_hours", "cause_type", "cause_text", "postmortem_url", "outlets", "links", "first_seen", "last_updated", "notes",
]
OPEN = {"watching", "store"}
MAX_LINKS = 8

# A follow-up article (published days after the outage) is attached to an open incident of the same
# service if it reads like a follow-up rather than a new outage.
FOLLOW_UP_RE = re.compile(r"\b(root cause|caused by|blamed|post-?mortem|incident report|investigation|FCC|regulator|"
                          r"explain(?:s|ed)?|credit|compensat\w+|apolog\w+|what (?:caused|happened)|after (?:last|this) week)", re.I)

CAUSE_CUE_RE = re.compile(r"\b(caused by|due to|blamed|root cause|because of|stemmed from|triggered by|traced to|result of)\b", re.I)
CAUSE_RULES = [  # first match wins
    ("Third-party / vendor", re.compile(r"\b(third[- ]party|vendor|supplier|upstream provider|outside provider)\b", re.I)),
    ("Change / config error", re.compile(r"\b(misconfigur\w*|config(?:uration)? (?:change|error|issue|update)|faulty update|bad update|"
                                          r"software update|routing change|network change|maintenance)\b", re.I)),
    ("Software bug", re.compile(r"\b(software (?:issue|bug|glitch|problem|error|failure)|bug|code (?:error|change)|technical glitch)\b", re.I)),
    ("Hardware / power", re.compile(r"\b(power (?:outage|failure|loss|issue)|fire|hardware (?:failure|fault|issue)|fib(?:er|re) cut|cable cut|"
                                     r"equipment failure)\b", re.I)),
]
DURATION_RES = [
    re.compile(r"\b(?:lasted|lasting|for|nearly|about|more than|over|almost|roughly|after)\s+(\d+(?:\.\d+)?)\s+hours?\b", re.I),
    re.compile(r"\b(\d+(?:\.\d+)?)[- ]hours?[- ]long\b", re.I),
]
POSTMORTEM_RE = re.compile(r"\b(post-?mortem|root cause|incident report|post-incident|RCA)\b", re.I)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def guess_cause(text: str) -> tuple[str, str] | None:
    """(cause_type, snippet) when the text states a cause, else None."""
    for sentence in re.split(r"(?<=[.!?])\s+|\s+-\s+", text or ""):
        if not CAUSE_CUE_RE.search(sentence):
            continue
        for label, pat in CAUSE_RULES:
            if pat.search(sentence):
                return label, sentence.strip()[:200]
    return None


def guess_duration(text: str) -> float | None:
    vals = [float(m) for pat in DURATION_RES for m in pat.findall(text or "")]
    vals = [v for v in vals if 0 < v <= 240]
    return max(vals) if vals else None


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: (r.get(k) or "") for k in TRACKING_FIELDS} for r in csv.DictReader(f)]


def save(path: Path, rows: list[dict]) -> None:
    """Open incidents first, newest first within each group."""
    rows.sort(key=lambda r: r["incident_date"], reverse=True)
    rows.sort(key=lambda r: r["status"] not in OPEN)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRACKING_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _find_open(rows, service: str, day: dt.date, text: str, max_age_days: int):
    """Open (or dismissed, to keep ignoring it) row this cluster belongs to."""
    best = None
    for r in rows:
        if r["service"] != service or r["status"] == "stored":
            continue
        d = dt.date.fromisoformat(r["incident_date"])
        gap = (day - d).days
        if abs(gap) <= 2 or (0 < gap <= max_age_days and r["status"] in OPEN and FOLLOW_UP_RE.search(text)):
            if best is None or abs(gap) < abs((day - dt.date.fromisoformat(best["incident_date"])).days):
                best = r
    return best


def update(rows: list[dict], clusters: list[dict], today: dt.date, watch_days: int) -> dict:
    """Apply this week's clusters to the watch list. Returns {new, updated, ignored} for the report."""
    report = {"new": [], "updated": [], "late": [], "ignored": 0}
    for c in clusters:
        text = " ".join(f"{r['title']} {r.get('text', '')}" for r in c["rows"])
        likely = c["sources"] >= 2 or c["status_page"]
        row = _find_open(rows, c["service"], c["first"], text, max_age_days=watch_days + 14)
        if row is None:
            late = _stored_match(rows, c["service"], c["first"], text)
            if late:
                report["late"].append((late, c["rows"]))
                continue
            if c["tracked"] or not likely:
                report["ignored"] += 1
                continue
            row = {k: "" for k in TRACKING_FIELDS}
            row.update(id=f"{c['first'].isoformat()}-{slug(c['service'])}", service=c["service"], category=c["category"],
                       incident_date=c["first"].isoformat(), status="watching", first_seen=today.isoformat(),
                       store_after=(c["first"] + dt.timedelta(days=watch_days)).isoformat(), sources="0", max_reports="")
            rows.append(row)
            report["new"].append(row)
            changes = ["new"]
        else:
            if row["status"] == "dismissed":
                report["ignored"] += 1
                continue
            changes = []
        _merge(row, c, text, changes)
        if changes:
            row["last_updated"] = today.isoformat()
            if changes[0] != "new":
                report["updated"].append((row, changes))
    return report


def _stored_match(rows, service: str, day: dt.date, text: str, window_days: int = 180):
    """A stored incident that this later coverage is following up on (e.g. a regulator report)."""
    if not FOLLOW_UP_RE.search(text):
        return None
    for r in rows:
        if r["service"] == service and r["status"] == "stored" and 0 <= (day - dt.date.fromisoformat(r["incident_date"])).days <= window_days:
            return r
    return None


def _merge(row: dict, c: dict, text: str, changes: list[str]) -> None:
    links = [l for l in row["links"].split(" | ") if l]
    for r in c["rows"]:
        if r["url"] not in links:
            links.append(r["url"])
    domains = {r["domain"] for r in c["rows"]} | set(filter(None, row["outlets"].split(" | ")))
    before = int(row["sources"] or 0)
    after = max(before, len(domains))
    if after > before and (not changes or changes[0] != "new"):
        changes.append(f"+{after - before} source{'s' * (after - before > 1)}")
    row["sources"] = str(after)
    row["outlets"] = " | ".join(sorted(domains))
    row["links"] = " | ".join(links[:MAX_LINKS])

    rep = c["max_reports"]
    if rep and rep > int(row["max_reports"] or 0):
        row["max_reports"] = str(rep)
        changes.append(f"reports {rep:,}")
    dur = guess_duration(text)
    if dur and dur > float(row["duration_hours"] or 0):
        row["duration_hours"] = f"{dur:g}"
        changes.append(f"duration {dur:g} h")
    cause = guess_cause(text)
    if cause and not row["cause_type"]:
        row["cause_type"], row["cause_text"] = cause
        changes.append(f"cause: {cause[0]}")
    if not row["postmortem_url"]:
        for r in c["rows"]:
            if POSTMORTEM_RE.search(f"{r['title']} {r.get('text', '')}"):
                row["postmortem_url"] = r["url"]
                changes.append("post-mortem found")
                break


def due(rows: list[dict], today: dt.date) -> list[dict]:
    return [r for r in rows if r["status"] == "store" or (r["status"] == "watching" and r["store_after"]
                                                             and dt.date.fromisoformat(r["store_after"]) <= today)]


def to_outage_row(r: dict, ai_era_start: dt.date) -> dict:
    d = dt.date.fromisoformat(r["incident_date"])
    reports = r["max_reports"]
    first_link = next((l for l in r["links"].split(" | ") if l), "")
    return {
        "date": r["incident_date"], "service": r["service"], "category": r["category"], "region": "Not stated",
        "duration_hours": r["duration_hours"], "duration_note": "from news coverage" if r["duration_hours"] else "",
        "reports": reports, "reports_type": "Peak (news)" if reports else "",
        "impact_note": f"Added by weekly scan from {r['sources']} source(s)" + (f"; {r['notes']}" if r["notes"] else ""),
        "cause_type": r["cause_type"] or "Not disclosed", "cause": r["cause_text"] or "Not disclosed",
        "era": "AI era" if d >= ai_era_start else "Pre-AI", "source": r["postmortem_url"] or first_link,
    }


def store_due(rows: list[dict], outages_csv: Path, today: dt.date, ai_era_start: dt.date) -> list[dict]:
    """Append due rows to outages.csv (skipping ones already there) and mark them stored."""
    with outages_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        existing = {(o["date"], o["service"]) for o in reader}
    stored = []
    with outages_csv.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        for r in due(rows, today):
            if (r["incident_date"], r["service"]) not in existing:
                w.writerow(to_outage_row(r, ai_era_start))
                existing.add((r["incident_date"], r["service"]))
            r["status"] = "stored"
            r["last_updated"] = today.isoformat()
            stored.append(r)
    return stored


def report_markdown(report: dict, stored: list[dict], rows: list[dict]) -> str:
    L = ["## Watch list (`data/tracking.csv`)", ""]
    watching = [r for r in rows if r["status"] in OPEN]
    L.append(f"{len(report['new'])} new, {len(report['updated'])} updated, {len(stored)} stored this week. "
             f"{len(watching)} still being watched.")
    L.append("")
    if stored:
        L.append("**Stored in `data/outages.csv` this week** (check them in this PR's diff):")
        L += [f"- {r['incident_date']} {r['service']}: cause {r['cause_type'] or 'not disclosed'}, "
              f"{'duration ' + r['duration_hours'] + ' h' if r['duration_hours'] else 'duration unknown'}, "
              f"{'peak ' + format(int(r['max_reports']), ',') + ' reports' if r['max_reports'] else 'no report count'}" for r in stored]
        L.append("")
    if report["new"]:
        L.append("**New this week:**")
        L += [f"- {r['incident_date']} {r['service']} ({r['category']}), stores on {r['store_after']}" for r in report["new"]]
        L.append("")
    if report["updated"]:
        L.append("**Updated this week:**")
        L += [f"- {r['incident_date']} {r['service']}: {', '.join(ch)}" for r, ch in report["updated"]]
        L.append("")
    if report["late"]:
        L.append("**New coverage of already-stored incidents** (update their rows in `data/outages.csv` by hand if it adds facts):")
        for r, items in report["late"]:
            links = " · ".join(f"[{i['domain']}]({i['url']})" for i in items[:3])
            L.append(f"- {r['incident_date']} {r['service']}: {links}")
        L.append("")
    if watching:
        L += ["| Incident | Service | Sources | Reports | Duration | Cause | Stores on |", "|---|---|---|---|---|---|---|"]
        for r in watching:
            when = "next run" if r["status"] == "store" else r["store_after"]
            rep = format(int(r["max_reports"]), ",") if r["max_reports"] else ""
            dur = f"{r['duration_hours']} h" if r["duration_hours"] else ""
            L.append(f"| {r['incident_date']} | {r['service']} | {r['sources']} | {rep} | {dur} | {r['cause_type'] or '?'} | {when} |")
        L.append("")
    L.append("To change one before it's stored, edit its row in `data/tracking.csv` in this PR: fix any value, set `status` "
             "to `store` (store now) or `dismissed` (false alarm), or move `store_after` later.")
    L.append("")
    return "\n".join(L)
