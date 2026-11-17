#!/usr/bin/env python3
"""Gather candidate outages from public sources for human review.

Runs weekly (see .github/workflows/weekly-scan.yml) or by hand:

    python scripts/gather_sources.py                 # last 7 days, writes data/candidates/
    python scripts/gather_sources.py --days 14       # longer lookback
    python scripts/gather_sources.py --dry-run       # print, write nothing

Sources (all free, no API keys), set in scripts/sources.json:
  * GDELT DOC 2.0 news search
  * Google News RSS search
  * Official status-page feeds (Cloudflare, Google Cloud, Azure, AWS, OpenAI, Anthropic)

Downdetector itself has no public API and its terms forbid scraping, so this script
does NOT read downdetector.com. It finds news coverage that cites Downdetector, plus
providers' own incident feeds.

It also keeps a watch list of new outages (data/tracking.csv, see scripts/tracking.py): likely
incidents are added, updated each week as more coverage appears (sources, report counts,
duration, cause, post-mortem link), and stored in data/outages.csv once their store_after
date passes (default 21 days after the outage). Follow-up searches run for each open incident.

Output (one set per run, dated by the run day in UTC):
  data/candidates/YYYY-MM-DD.csv  every matching article or status entry
  data/candidates/YYYY-MM-DD.md   the same, grouped into likely incidents, for review
  data/candidates/latest.md       copy of the newest summary (used as the PR body)
  data/candidates/seen_urls.txt   URLs already reported, so weekly runs don't repeat

Nothing is added to data/outages.csv automatically. A person checks each candidate,
confirms duration, report counts and cause against a primary source, and adds a row.
Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracking  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "scripts" / "sources.json"
DEFAULT_OUT = ROOT / "data" / "candidates"
OUTAGES_CSV = ROOT / "data" / "outages.csv"
TRACKING_CSV = ROOT / "data" / "tracking.csv"
AI_ERA_START = dt.date(2022, 11, 30)

CANDIDATE_FIELDS = [
    "incident_date", "service", "category", "signal", "title", "url", "domain",
    "published_utc", "reports_mentioned", "already_tracked", "query",
]


# --------------------------------------------------------------------------- fetch
class Fetcher:
    """HTTP GET with retries. In tests, pass fixtures={url_substring: path}."""

    def __init__(self, user_agent: str, fixtures: dict[str, Path] | None = None):
        self.user_agent = user_agent
        self.fixtures = fixtures or {}
        self.errors: list[str] = []

    def get(self, url: str, retries: int = 2, timeout: int = 30) -> bytes | None:
        for key, path in self.fixtures.items():
            if key in url:
                return Path(path).read_bytes()
        if self.fixtures:  # offline mode: unknown URL
            self.errors.append(f"no fixture for {url}")
            return None
        last = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                    break
                time.sleep(3 * (attempt + 1))
        self.errors.append(f"{url} -> {last}")
        return None


# --------------------------------------------------------------------------- helpers
def parse_date(text: str | None) -> dt.datetime | None:
    """Parse RFC 822, ISO 8601 or GDELT's 20251020T123000Z into an aware UTC datetime."""
    if not text:
        return None
    text = text.strip()
    m = re.fullmatch(r"(\d{8})T(\d{6})Z", text)
    if m:
        return dt.datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    try:
        d = email.utils.parsedate_to_datetime(text)
        if d is not None:
            return d.astimezone(dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d.astimezone(dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def normalize_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith("utm_")])
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), query, ""))


def domain_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|m|thousand|k)?"
REPORTS_RE = re.compile(_NUM + r"\+?\s+(?:outage\s+|user\s+|problem\s+)?(?:reports|users reported|people reported|complaints)", re.I)


def reports_mentioned(text: str) -> int | None:
    """Largest '<number> reports' figure in the text, e.g. '17 million reports' -> 17000000."""
    best = None
    for num, unit in REPORTS_RE.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        v *= 1_000_000 if unit in ("million", "m") else 1_000 if unit in ("thousand", "k") else 1
        best = max(best or 0, int(v))
    return best


def build_matchers(services: dict) -> list[tuple[str, str, re.Pattern]]:
    out = []
    for name, spec in services.items():
        aliases = sorted(spec.get("aliases", [name]), key=len, reverse=True)
        # Case-sensitive, word-bounded: brand names are capitalized, which avoids "car chase" etc.
        pat = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in aliases) + r")(?![\w-])")
        out.append((name, spec.get("category", ""), pat))
    return out


def match_services(text: str, matchers) -> list[tuple[str, str]]:
    return [(name, cat) for name, cat, pat in matchers if pat.search(text or "")]


def looks_like_outage(text: str, cfg: dict) -> bool:
    low = (text or "").lower()
    if any(x in low for x in cfg.get("exclude_keywords", [])):
        return False
    return any(re.search(r"\b" + re.escape(k) + r"\b", low) for k in cfg.get("outage_keywords", []))


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


# --------------------------------------------------------------------------- sources
def from_gdelt(cfg: dict, days: int, fetch: Fetcher, sleep=time.sleep) -> list[dict]:
    g = cfg.get("gdelt", {})
    if not g.get("enabled"):
        return []
    items = []
    for i, q in enumerate(g.get("queries", [])):
        if i:
            sleep(g.get("pause_seconds", 6))
        params = {"query": q, "mode": "artlist", "format": "json", "sort": "datedesc",
                  "maxrecords": str(g.get("max_records", 250)), "timespan": f"{days}d"}
        raw = fetch.get(g["endpoint"] + "?" + urllib.parse.urlencode(params))
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            fetch.errors.append(f"GDELT returned non-JSON for query {q!r}: {raw[:120]!r}")
            continue
        for a in data.get("articles", []):
            items.append({"signal": "news (GDELT)", "title": a.get("title", ""), "url": a.get("url", ""),
                          "published": parse_date(a.get("seendate")), "summary": "", "query": q})
    return items


def from_google_news(cfg: dict, days: int, fetch: Fetcher, sleep=time.sleep, extra_queries=()) -> list[dict]:
    g = cfg.get("google_news", {})
    if not g.get("enabled"):
        return []
    items = []
    for i, q in enumerate(list(g.get("queries", [])) + list(extra_queries)):
        if i:
            sleep(g.get("pause_seconds", 2))
        params = {"q": f"{q} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
        raw = fetch.get(g["endpoint"] + "?" + urllib.parse.urlencode(params))
        if not raw:
            continue
        for e in parse_feed(raw, fetch, label=f"Google News {q!r}"):
            title = e["title"]
            # Google News titles end with " - Publisher"; keep the publisher as the domain hint.
            items.append({"signal": "news (Google News)", "title": title, "url": e["link"],
                          "published": e["published"], "summary": e["summary"], "query": q,
                          "source_name": e.get("source", "")})
    return items


def from_status_feeds(cfg: dict, days: int, fetch: Fetcher, now: dt.datetime) -> list[dict]:
    items = []
    cutoff = now - dt.timedelta(days=days)
    for feed in cfg.get("status_feeds", []):
        raw = fetch.get(feed["url"])
        if not raw:
            continue
        for e in parse_feed(raw, fetch, label=feed["url"]):
            if e["published"] and e["published"] < cutoff:
                continue
            text = e["title"] + " " + e["summary"]
            if re.search(r"\b(scheduled|maintenance)\b", text, re.I) and not re.search(r"\b(outage|degrad|error|disrupt|incident)", text, re.I):
                continue
            items.append({"signal": "status page", "title": e["title"], "url": e["link"] or feed["url"],
                          "published": e["published"], "summary": e["summary"], "query": feed["url"],
                          "service": feed["service"], "category": feed.get("category", "")})
    return items


def parse_feed(raw: bytes, fetch: Fetcher, label: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into [{title, link, published, summary, source}]."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        fetch.errors.append(f"{label}: not valid XML ({e})")
        return []
    def local(tag):
        return tag.rsplit("}", 1)[-1]
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "link": "", "published": None, "summary": "", "source": ""}
        for ch in el:
            t = local(ch.tag)
            if t == "title":
                rec["title"] = strip_html("".join(ch.itertext()))
            elif t == "link":
                rec["link"] = ch.get("href") or (ch.text or "").strip() or rec["link"]
            elif t in ("pubDate", "published", "updated") and rec["published"] is None:
                rec["published"] = parse_date(ch.text)
            elif t in ("description", "summary", "content"):
                rec["summary"] = strip_html("".join(ch.itertext()))[:600]
            elif t == "source":
                rec["source"] = (ch.text or "").strip()
        out.append(rec)
    return out


# --------------------------------------------------------------------------- pipeline
def load_tracked(path: Path) -> list[tuple[dt.date, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [(dt.date.fromisoformat(r["date"]), r["service"]) for r in csv.DictReader(f) if r.get("date")]


def is_tracked(day: dt.date, service: str, tracked) -> bool:
    s = service.lower().split(" ")[0]
    return any(abs((day - d).days) <= 2 and s in name.lower() for d, name in tracked)


def collect(cfg: dict, days: int, fetch: Fetcher, now: dt.datetime, seen: set[str], tracked, sleep=time.sleep,
            follow_ups=()) -> list[dict]:
    raw_items = (from_gdelt(cfg, days, fetch, sleep) + from_google_news(cfg, days, fetch, sleep, follow_ups)
                 + from_status_feeds(cfg, days, fetch, now))
    matchers = build_matchers(cfg.get("services", {}))
    cutoff = now - dt.timedelta(days=days)
    rows, urls = [], set()
    for it in raw_items:
        url = it["url"]
        if not url:
            continue
        key = normalize_url(url)
        # Status pages edit the same incident URL as they learn more (e.g. add a post-mortem),
        # so those are re-read every run; news URLs are only reported once.
        if key in urls or (key in seen and it["signal"] != "status page"):
            continue
        pub = it.get("published")
        if pub and pub < cutoff:
            continue
        text = f"{it['title']} {it.get('summary', '')}"
        if it["signal"] != "status page" and not looks_like_outage(text, cfg):
            continue
        hits = [(it["service"], it["category"])] if it.get("service") else match_services(text, matchers)
        if not hits:
            continue
        urls.add(key)
        day = (pub or now).date()
        dom = it.get("source_name") or domain_of(url)
        for service, category in hits:
            rows.append({
                "incident_date": day.isoformat(), "service": service, "category": category,
                "signal": it["signal"], "title": it["title"], "url": url, "domain": dom,
                "published_utc": pub.strftime("%Y-%m-%d %H:%M") if pub else "",
                "reports_mentioned": reports_mentioned(text) or "",
                "already_tracked": "yes" if is_tracked(day, service, tracked) else "",
                "query": it.get("query", ""),
                "text": it.get("summary", ""),
                "seen_before": key in seen,
            })
    rows.sort(key=lambda r: (r["incident_date"], r["service"], r["domain"]), reverse=True)
    return rows


def cluster(rows: list[dict]) -> list[dict]:
    """Group rows into likely incidents: same service within a 2-day window."""
    by_service = defaultdict(list)
    for r in rows:
        by_service[r["service"]].append(r)
    clusters = []
    for service, rs in by_service.items():
        rs = sorted(rs, key=lambda r: r["incident_date"])
        cur = None
        for r in rs:
            d = dt.date.fromisoformat(r["incident_date"])
            if cur and (d - cur["last"]).days <= 2:
                cur["rows"].append(r); cur["last"] = d
            else:
                cur = {"service": service, "category": r["category"], "first": d, "last": d, "rows": [r]}
                clusters.append(cur)
    for c in clusters:
        c["sources"] = len({r["domain"] for r in c["rows"]})
        c["status_page"] = any(r["signal"] == "status page" for r in c["rows"])
        c["max_reports"] = max([int(r["reports_mentioned"]) for r in c["rows"] if r["reports_mentioned"]] or [0])
        c["tracked"] = any(r["already_tracked"] for r in c["rows"])
    clusters.sort(key=lambda c: (c["status_page"] or c["sources"] >= 2, c["sources"], c["max_reports"]), reverse=True)
    return clusters


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def summary_markdown(clusters, rows, errors, days, now) -> str:
    strong = [c for c in clusters if (c["sources"] >= 2 or c["status_page"]) and not c["tracked"]]
    weak = [c for c in clusters if c not in strong and not c["tracked"]]
    tracked = [c for c in clusters if c["tracked"]]
    L = [f"# Outage candidates, {now:%Y-%m-%d}", "",
         f"Scan of the last {days} days: {len(rows)} matching articles or status entries, grouped into {len(clusters)} possible incidents.",
         "", "Likely incidents go on the watch list automatically and are stored in `data/outages.csv` after "
         "their watch period. Single-source mentions don't; to keep one, add a row to `data/tracking.csv` "
         "(or straight to `data/outages.csv`).", ""]

    def table(cs):
        L.extend(["| Dates | Service | Industry | Sources | Reports cited | Top links |", "|---|---|---|---|---|---|"])
        for c in cs:
            dates = c["first"].isoformat() if c["first"] == c["last"] else f"{c['first']} – {c['last']}"
            links = " · ".join(f"[{md_escape(r['domain'])}]({r['url']})" for r in c["rows"][:3])
            rep = f"{c['max_reports']:,}" if c["max_reports"] else ""
            sp = " (status page)" if c["status_page"] else ""
            L.append(f"| {dates} | {md_escape(c['service'])} | {c['category']} | {c['sources']}{sp} | {rep} | {links} |")
        L.append("")

    L.append(f"## Likely incidents ({len(strong)})\nReported by 2+ outlets or on the provider's own status page.\n")
    table(strong) if strong else L.append("_None this week._\n")
    L.append(f"## Single-source mentions ({len(weak)})\nOften minor or local. Check before adding.\n")
    table(weak) if weak else L.append("_None._\n")
    if tracked:
        L.append(f"## Already in the dataset ({len(tracked)})\n")
        table(tracked)
    if errors:
        L.append("## Sources that failed this run\n")
        L.extend(f"- `{e[:200]}`" for e in errors)
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--days", type=int, default=None, help="lookback window (default: lookback_days in config)")
    ap.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    ap.add_argument("--fixtures", type=Path, default=None, help="JSON map {url_substring: file} for offline tests")
    ap.add_argument("--now", default=None, help="override current UTC time (ISO), for tests")
    ap.add_argument("--tracking", type=Path, default=TRACKING_CSV, help="watch list CSV")
    ap.add_argument("--outages", type=Path, default=OUTAGES_CSV, help="dataset CSV that stored incidents are added to")
    args = ap.parse_args(argv)

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    days = args.days or cfg.get("lookback_days", 7)
    now = parse_date(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    fixtures = None
    if args.fixtures:
        base = args.fixtures.parent
        fixtures = {k: base / v for k, v in json.loads(args.fixtures.read_text()).items()}
    fetch = Fetcher(cfg.get("user_agent", "ai-outage-tracker"), fixtures)

    seen_path = args.out / "seen_urls.txt"
    seen = set(seen_path.read_text().split()) if seen_path.exists() else set()
    watch = tracking.load(args.tracking)
    open_rows = [r for r in watch if r["status"] in tracking.OPEN]
    follow_ups = [f'"{r["service"]}" outage cause' for r in open_rows][: cfg.get("max_follow_ups", 15)]
    rows = collect(cfg, days, fetch, now, seen, load_tracked(args.outages),
                   sleep=(lambda s: None) if fixtures else time.sleep, follow_ups=follow_ups)
    today = now.date()
    # Every row (including re-read status-page entries) can update the watch list...
    report = tracking.update(watch, cluster(rows), today, cfg.get("watch_days", 21))
    stored = [] if args.dry_run else tracking.store_due(watch, args.outages, today, AI_ERA_START)
    # ...but only links not reported before are listed as new leads.
    rows = [r for r in rows if not r["seen_before"]]
    clusters = cluster(rows)
    md = summary_markdown(clusters, rows, fetch.errors, days, now)
    md = md.replace("## Likely incidents", tracking.report_markdown(report, stored, watch) + "\n## Likely incidents", 1)

    if args.dry_run:
        print(md)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%d")
        with (args.out / f"{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CANDIDATE_FIELDS, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
        (args.out / f"{stamp}.md").write_text(md, encoding="utf-8")
        (args.out / "latest.md").write_text(md, encoding="utf-8")
        tracking.save(args.tracking, watch)
        seen |= {normalize_url(r["url"]) for r in rows}
        seen_path.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
        print(f"Wrote {len(rows)} rows, {len(clusters)} possible incidents to {args.out}/{stamp}.*; "
              f"watch list: {len(report['new'])} new, {len(report['updated'])} updated, {len(stored)} stored")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(md + "\n")

    for e in fetch.errors:
        print("WARN:", e, file=sys.stderr)
    total_sources = (len(cfg.get("gdelt", {}).get("queries", [])) * bool(cfg.get("gdelt", {}).get("enabled"))
                     + len(cfg.get("google_news", {}).get("queries", [])) * bool(cfg.get("google_news", {}).get("enabled"))
                     + len(cfg.get("status_feeds", [])))
    if total_sources and len(fetch.errors) >= total_sources:
        print("ERROR: every source failed; check network access.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
