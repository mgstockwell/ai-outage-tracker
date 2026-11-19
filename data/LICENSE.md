# Data license: Creative Commons Attribution 4.0 International (CC BY 4.0)

Copyright (c) 2026 Mark Stockwell

The dataset in this folder (`outages.csv`, `outages.json`, `yearly_largest.csv`, `yearly_largest.json`,
`analysis.json`, `tracking.csv` and the files under `candidates/`) is licensed under the
**Creative Commons Attribution 4.0 International License**.

Full legal text: https://creativecommons.org/licenses/by/4.0/legalcode
Summary: https://creativecommons.org/licenses/by/4.0/

## You are free to

- **Share**: copy and redistribute the material in any medium or format, for any purpose, even commercially.
- **Adapt**: remix, transform and build upon the material for any purpose, even commercially.

## Under these terms

- **Attribution**: give appropriate credit, provide a link to the license, and indicate if changes were made.
  You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.
- **No additional restrictions**: you may not apply legal terms or technological measures that legally restrict
  others from doing anything the license permits.

## Suggested credit

> AI Outage Tracker dataset by Mark Stockwell, https://github.com/mgstockwell/ai-outage-tracker, licensed CC BY 4.0.

## Not covered: `updog/`

`data/updog/` is an archive of outage detections published by Updog by Datadog (https://updog.ai). That data
belongs to Datadog and is **not** licensed under CC BY 4.0 by this project. See [`updog/README.md`](updog/README.md).

## What this license covers

It covers this compilation: the selection, structure, labels (such as `cause_type` and `era`) and notes.
The underlying facts (dates, durations, report counts and causes) come from the publications linked in each row's
`source` column. Downdetector report figures are as published by Ookla/Downdetector or quoted in news coverage.
This project is not affiliated with or endorsed by Downdetector, Ookla or any company listed.

The code in this repository (`index.html`, `assets/`, `scripts/`, `tests/`) is licensed separately under the
MIT License in [`../LICENSE`](../LICENSE). `vendor/d3.min.js` is D3, under its own ISC license in
[`../vendor/d3.LICENSE`](../vendor/d3.LICENSE).
