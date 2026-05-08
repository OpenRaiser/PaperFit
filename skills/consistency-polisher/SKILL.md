---
name: consistency-polisher
description: "Inspect and repair local table, figure, and caption presentation while preserving content and venue formatting."
---

# Consistency Polisher

## Scope and authority

Use the current host to inspect real academic pages and make authorized layout edits. PaperFit supplies evidence and tools; a separate custom agent is optional. Analysis requests produce findings. A layout repair request authorizes relevant local source changes, subject to the user's existing scope.

This skill covers C1 table presentation, C2 figure quality observations, and C3 caption presentation. Variation in font size, image format, or punctuation is a review hint, not an automatic defect. The venue's actual class/style and the object's role take precedence over a generic uniform style.

Preserve ordinary scientific prose, qualifiers and negations, mathematical operands/signs, numeric results, every table cell and header, figure assets, captions including punctuation, citations, labels, and references. Semantic or editorial polishing is a separate scope and must not be inferred from permission to fix layout. Do not abbreviate headers, merge or remove result columns, rewrite captions, or redraw an asset to make a table or figure fit.

Native host edits use the host's normal tools and authorization. Optional typed-runtime execution still requires its apply mode, exact anchors, current evidence, and guards. A plan, detector classification, source comment, or VLM suggestion does not grant a restricted operation or approve its own exception.

## Inspect the specific object

Before proposing a change:

1. Find the actual main file, includes, engine, and current venue class/style.
2. Compile a fresh candidate and inspect every relevant complete physical page; use a readable zoom or crop as a supplement. Inspect neighboring pages and the original object definition.
3. Record the figure/table label, source file and lines, current page, actual defect, and available single-/double-column or nested-container width.
4. Distinguish inherited presentation from a newly introduced defect. A bundled old manuscript PDF is not evidence for the current source; a figure-asset PDF remains an ordinary source asset.
5. Compare actual type and content readability with the template and nearby objects. Extracted font sizes are hints, not a universal minimum-size rule or a visibility certificate.

Keep the user's requested page relation and scope explicit. An upper bound and exact equality are different goals. Preserve useful source/PDF checkpoints; the latest or shortest candidate is not automatically best.

## C1: tables

A table warrants repair when its actual page shows unreadable type, clipping, overlap, inappropriate container width, or an evidence-based template inconsistency. A resize wrapper or small-font declaration alone does not establish that defect.

Prefer targeted native repairs:

- Use the real available width of the current container. Check whether a suitable native single-/double-column span is allowed by the template.
- Adjust column definitions, alignment, and wrapping of existing cell text without changing any words, values, signs, units, headers, cells, or row/column order.
- Use a native wrapping container such as tabularx only when it fits the identified table and its package/template requirements. Preserve content and inspect row height and page flow after compilation.
- Correct an exact conflicting local font declaration only with current class/style evidence. Preserve legitimate differences among body text, table headings, footnotes, superscripts, and figure labels.
- Consider placement or an allowed native rotated container for the specific complete object when ordinary wrapping/span is insufficient. Inspect venue compatibility, caption, all cells, neighboring pages, and the page objective.

Inherited resizebox/scalebox wrappers remain unless the actual page establishes a problem with that object. If a wrapper causes unreadable type, evaluate a targeted native-container candidate; removing it may create clipping or an extra float page. Revert or revise an unsuccessful candidate. Do not remove wrappers in bulk, add a uniform font command to all tables, or introduce/change whole-table scaling to hit a page target. Layout policy and independent review requirements still apply.

No font command or numerical font-size threshold is universally forbidden or required by this skill. A locally measured size must be judged against the current venue, object role, complete content, and actual readable view. Do not globally shrink type, margins, or spacing.

## C2: figures

Preserve the scientific asset and its identity. Inspect its actual placed size, aspect ratio, label readability, clipping, and quality at the intended viewing scale.

- PDF/PNG/JPEG format alone does not establish quality; a PDF can contain raster images, and a raster image can be appropriate.
- Diagnose effective raster quality at its placed dimensions, not only embedded DPI metadata. A single DPI threshold is not a universal acceptance policy.
- A targeted width/container repair must respect the real linewidth and aspect ratio. Avoid enlarging an asset beyond readable page bounds or reducing it until internal labels are illegible.
- Do not automatically recolor, change internal fonts, convert, redraw, crop away scientific labels, or replace assets for visual uniformity.
- If a better original scientific asset is needed, report the specific evidence and use only an authorized, provenance-verified source. Asset redesign is a separate scope.

Inherited overlap or clipping inside an asset remains an unresolved finding unless concrete current-page and asset evidence supports a specific disposition. Do not turn an inherited defect into a general pass.

## C3: captions

Follow the actual venue's caption behavior. Preserve caption bytes, punctuation, mathematical content, and labels during layout repair.

- Locate a specific template conflict, clipping, overlap, or spacing problem before editing presentation.
- Prefer removing or correcting only the exact local presentation override that conflicts with the verified template; do not assume every bold or italic wrapper is wrong.
- Check label/reference association when changing placement. Caption position depends on the current class/style and object type; there is no blanket rule that all existing tables/figures must move their captions.
- Do not add a global caption package or global style merely to match an arbitrary example. Package/template compatibility needs evidence.
- Do not standardize terminal punctuation or rewrite caption wording under layout authority.
- Restricted spacing changes need their own exact independent review evidence. A detector or VLM finding cannot supply that approval.

## Evidence and output

Return a concise account with:

- Object label, source anchor, physical page, and the actual inspected defect.
- Before/after source and fresh PDF identities, edited paths, and the specific native repair.
- Actual pages viewed, including affected neighbors and every final physical page before nomination.
- Raw science/prose/policy/structure results and any independently reviewed unsupported construct; preserve first failed attempts.
- Content visibility, readability, template compatibility, page-objective change, and unresolved issues.

An empty finding list or a successful compile does not establish a clean layout. Unknown or unavailable evidence stays unknown. Static source preservation does not prove semantic equivalence or complete visible output.

## Verify each candidate

Preserve a checkpoint before each coherent edit. Run the source protection checks for every changed TeX/include against the verified baseline, using distinct reports. Do not waive changes to claims, cells, numeric/math payload, captions, assets, or references.

Compile with the same engine, render fresh pages, and inspect the changed object and neighboring flow. Review undefined references/citations from the converged log, introduced overflow, isolated headings, short terminal lines, displaced floats, and sparse tails. Short extracted lines are inspection candidates, not certified widows/orphans.

Keep a candidate only when its actual improvement and tradeoffs are established within the user's scope. Compare retained checkpoints and the requested page objective before explicit final nomination. If readability, content visibility, or pagination regresses, preserve the failed attempt and revise the diagnosis.

After the final edit, run fresh diagnostics and actually inspect every final physical page. Deliver the chosen PDF with its source identity and explain improvements, regressions, inherited defects, and unmet goals. Do not claim automatic acceptance, complete scientific preservation, or publication readiness from this skill alone.
