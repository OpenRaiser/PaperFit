# Category E Playbook

Category E covers template migration defects: `E1/E2/E3`.

## Primary Objective

Migrate a paper between templates while preserving semantics, bibliography integrity, and float usability.

## Required Inputs

- source project root
- target template kit
- template registry metadata
- rendered page images after migration

## Migration Safety Rules

1. Apply template changes as a staged transaction, not as scattered ad hoc edits.
2. Preserve bibliography commands and bibliography files throughout migration.
3. Finish macro compatibility before fine-grained layout polishing.
4. Re-run Category B and D checks after any column-mode change.

## Allowed Operations

- documentclass replacement
- template macro/package reconciliation
- figure/table environment migration between single-column and double-column modes
- page-budget planning throughout migration, with acceptance measured on a stable current build

## Forbidden Operations

- mixing source and target template macros in a half-migrated state
- manually dropping references, appendices, or acknowledgements to satisfy the target layout
- claiming migration success before visual validation in the target template

## Family-Specific Rules

### E1

- Single-column to double-column migration requires explicit width policy review for every major float.
- Wide figures or tables should move to `figure*` / `table*` when justified.
- Narrow leftover floats should be reconstructed, not simply centered with large whitespace.

### E2

- Plan page count alongside template, float widths and equations from the start; accept the objective only after a stable current build and actual page review.
- Use the user's exact/at_most policy and scope, not a typical template page count. A one-page exact miss is unresolved; an under-limit paper does not require filler or automatic prose expansion.
- Reuse Category A guidance for length balancing.

### E3

- Fix macro/package incompatibilities before visual tuning.
- Prefer template-native facilities over custom compatibility hacks.

## Exit Criteria

- target template compiles cleanly
- float sizing matches target column geometry
- bibliography is complete
- remaining issues are ordinary A/B/C/D polish, not migration breakage
