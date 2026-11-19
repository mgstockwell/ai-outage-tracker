# Updog by Datadog archive

Source: **[Updog by Datadog](https://updog.ai)**, which detects degradations of 30+ SaaS providers and AWS services
from anonymized telemetry across Datadog's customers. Updog itself shows only about the last 90 days, so the weekly
scan saves its public data file here and keeps every detection, building a history that grows from the first run.

| File | What it is |
|---|---|
| `outages.csv` | One row per detection: provider, start and end (UTC), minutes, status, kind (error / latency), region, affected endpoints, matching status-page incidents, and when this archive first and last saw it. New detections are added each week; ongoing ones are updated when they resolve; old ones are never removed. |
| `raw/YYYY-MM-DD.json.gz` | The unedited file downloaded each week, kept as evidence of what Updog published. |

**How this differs from `../outages.csv`.** Updog flags any measurable error or latency spike in a provider's API,
many lasting minutes and invisible to consumers. `../outages.csv` holds major consumer-facing outages that made the
news. The two are kept apart so the before/after findings aren't skewed. The weekly scan uses Updog only to add a
measured start and end time to matching outages on the watch list (`../tracking.csv`, columns `updog_match` and
`duration_source`), for providers both cover (see `providers_for_service` in `scripts/sources.json`).

**Rights.** This data is Datadog's. It is not covered by this repository's CC BY 4.0 data license or MIT code license.
Updog publishes no reuse terms that we found; it is archived here, unmodified, with credit, for research. If you reuse
it, credit "Updog by Datadog (updog.ai)". This project is not affiliated with Datadog.
