# Category C Playbook

Category C covers consistency defects: `C1/C2/C3/C4`.

## Primary Objective

Make the paper look intentionally uniform across tables, figures, captions, and local style decisions.

## Required Inputs

- rendered page images
- current `.tex`
- target template style expectations

## Style Anchor Rule

Choose one high-quality in-document anchor for each of the following:

- table typography
- figure sizing behavior
- caption formatting

Normalize outliers toward the anchor instead of making every object unique.

## Allowed Operations

- remove inconsistent local table scaling
- normalize caption spacing and format
- standardize local font-size declarations in floats
- standardize width policy across similar figures/tables

## Forbidden Operations

- content edits disguised as consistency work
- inconsistent one-off float styling that ignores the rest of the paper
- leaving a single table on visibly smaller text because it was “already compiling”

## Family-Specific Rules

### C1

- Remove `\resizebox`-induced typography drift first.
- Prefer width-aware reconstruction to font shrinking.

### C2

- Usually diagnose and report rather than auto-regenerate source images.
- If replacement assets exist locally, keep format and sizing policy consistent.

### C3

- Enforce one caption order, one punctuation style, and one spacing policy.
- Respect template-provided caption styling before introducing custom formatting.

### C4

- Treat abnormal caption gaps as a local layout inconsistency, often coupled with B2.
- Normalize object-caption spacing only after the float width is correct.

## Exit Criteria

- similar objects follow the same width and typography policy
- captions feel uniform page to page
- no table remains as an obvious outlier in font size or spacing
