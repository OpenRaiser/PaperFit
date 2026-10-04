# Category A Playbook

Category A covers space utilization defects: `A1/A2/A3/A4/A5`.

## Primary Objective

Improve whitespace distribution without damaging content integrity, figure readability, or bibliography completeness.

## Required Inputs

- current main `.tex`
- rendered page images
- `data/visual_signal_report.json`
- page budget target when `A3` is involved

## Allowed Operations

- paragraph-scoped `\looseness`
- paragraph-scoped `\emergencystretch`
- local spacing normalization
- float rebalancing through Category B workflow
- minimal semantic polish only as the last resort

## Forbidden Operations

- repeated accumulation of the same `\vspace` patch
- global destructive margin hacks
- default figure shrinking to fake page-budget compliance
- edits that disturb bibliography anchors or reference completeness

## Family-Specific Rules

### A1

- Prefer paragraph-level line breaking controls.
- If semantic edits are needed, keep them local and meaning-preserving.

### A2

- Prefer float redistribution before text edits.
- A trailing page with references is not an automatic defect if bibliography is intact.

### A3

- Do not use figure shrinking as the default first-line strategy.
- Prefer removing true over-width objects, redundant spacing, and bad float placement.
- If still over budget, escalate to controlled semantic compression.

### A4

- Treat end-of-page column imbalance as a coupled A/B problem.
- Inspect whether a float, heading, or equation is causing the asymmetric stop.

### A5

- Investigate whether a `figure*`, `table*`, restrictive float position, or bad source ordering created the hole.
- Fix the upstream float cause before adding text-level padding.

## Exit Criteria

- no obvious widow/orphan line remains when the defect was targeted
- no new large white hole is introduced
- no figure is visibly over-shrunk
- bibliography remains untouched
