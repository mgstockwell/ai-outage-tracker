# Candidates

Output of the weekly scan (`scripts/gather_sources.py`). Each run writes:

- `YYYY-MM-DD.csv`: every news article or status-page entry that looked like an outage of a tracked service
- `YYYY-MM-DD.md`: the same, grouped into likely incidents
- `latest.md`: the newest summary (used as the pull request description)
- `seen_urls.txt`: links already reported, so the next run only shows new ones

These are leads. Likely incidents also go on the watch list (`../tracking.csv`), where they collect updates
for a few weeks before being stored in `../outages.csv`. Nothing in this folder is shown on the dashboard.
