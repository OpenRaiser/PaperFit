# Category D Playbook

Category D covers overflow and alignment defects: `D1/D2/D3`.

## Primary Objective

Eliminate overfull content without corrupting formulas, references, or table readability.

## Required Inputs

- LaTeX compile log
- current `.tex`
- rendered page images for visual confirmation

## Allowed Operations

- add safe discretionary breakpoints
- convert equations to `multline`, `align`, or `split`
- reconstruct overflowing tables with width-aware environments
- enable URL line-breaking support where appropriate

## Forbidden Operations

- semantically altering a formula to make it shorter
- hiding equation overflow by shrinking the whole equation block
- compressing tables with `\resizebox` as the default response

## Family-Specific Rules

### D1

- Distinguish paragraph overflow from table overflow and from equation overflow.
- Fix the actual source object, not just the nearest surrounding text.

### D2

- Break formulas at semantically valid operators.
- Preserve numbering behavior.
- Prefer `split` inside `equation` if the paper expects one equation number.

### D3

- Use URL-aware breaking tools first.
- For long identifiers, add explicit break opportunities only where syntax remains valid.

## Validation Rule

After a D-family fix:

1. compile again;
2. ensure the warning count decreases or the targeted warning disappears;
3. confirm the page image no longer shows visible spillover.
