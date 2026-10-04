# Category B Playbook

Category B covers float defects: `B1/B2/B3/B4`.

## Primary Objective

Keep figures and tables close to their semantic anchors and sized appropriately for the target column geometry.

## Required Inputs

- current main `.tex`
- `data/crossrefs.json`
- rendered page images
- target template column mode

## Required Invariants

1. Float moves must not cross the bibliography boundary.
2. Float fixes must pass hard content-integrity gating.
3. Table-width fixes must preserve readable table typography.

## Allowed Operations

- normalize float placement params toward `[ht]` or similar
- add `\FloatBarrier` when justified
- move float source blocks closer to the first reference
- convert plain narrow tables to `tabular*{\\linewidth}` or `tabular*{\\textwidth}`
- convert text-heavy tables to `tabularx`
- promote wide objects to `figure*` / `table*` when the template is double-column

## Forbidden Operations

- moving floats below `\bibliography{...}` or `\begin{thebibliography}`
- `\resizebox` as the default width fix
- detaching a table from the paragraph that introduces it
- claiming success from source edits alone without page-image validation

## Family-Specific Rules

### B1

- Source distance and visual distance are different diagnostics.
- If source distance is already short, prefer float-parameter and barrier fixes.
- If source distance is long, use semantic-anchor-aware source movement.

### B2

- For plain alignment tables, prefer `tabular*` with `@{\extracolsep{\fill}}`.
- For text-heavy tables, prefer `tabularx`.
- For cross-column width problems, use `\textwidth` with `table*` rather than shrinking text.

### B3

- Break float clusters by restoring text flow or varying float destinations.
- A cluster fix is not valid if it creates a new A5 hole elsewhere.

### B4

- Prefer environment changes such as `longtable` or controlled multi-line layouts.
- Avoid manual page-breaking around floats unless no safer option exists.

## Exit Criteria

- targeted float is visually near its introduction
- narrow or over-wide float is corrected for its column mode
- no float appears after the bibliography start
