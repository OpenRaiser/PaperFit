# Page fitting with the editing host

PaperFit supplies anchors, measurements and candidate checks. The existing
Codex, Claude Code or Cursor host chooses edits, inspects images and decides
what to retain. This toolbox does not replace the host with a repair harness.

## Establish the objective and plan

An upper bound and an exact target are different. For exact N, both N+1 and
N-1 remain unfinished. Count the requested scope: body, body plus references,
or the full PDF including appendices. Physical PDF counts cannot establish
body/reference boundaries. Resolve unknown boundaries rather than reporting pass.

Plan page count, readability, native-template compliance and complete visible
content together. Use the source inventory early, including when compilation
needs repair. Check broken native-template overrides, large explicit gaps and
float queues before spending a small budget on micro adjustments. The command
provides opportunities for the host to judge; it does not prescribe an edit:

    paperfit page-fit --main paper.tex --target-pages 12 --page-policy exact --page-budget-scope with_appendix --output .paperfit/reports/page_fit_01.json

The report separates observed_pdf_objective (PDF count/direction) from
page_objective (source-bound goal), source_inventory (real include files,
hashes, objects and lines), tiers (intervention families; the legacy field name is not a fixed sequence), and
review_required (every physical page, including pages without findings).

Source opportunities can inform planning without a usable PDF:
`layout_plan.available_before_defects_stable` and `planning_review_candidates`
are separate from final acceptance. Each candidate asks the host to verify it;
the magnitude of a source gap alone does not prove a defect or page saving.
Unknown source/PDF binding is not success.
The phase is needs_current_build
until current evidence exists, even if the old PDF has exactly the desired count. For an isolated build pass --pdf with
its exact freshly compiled PDF, retain the compiler invocation/log and source
snapshot, and independently verify the build. --data-dir selects existing
runtime evidence. An old PDF, clean log, VLM DONE or empty findings cannot
clear the current paper. --defects-stable records host review; it does not
automatically certify defects absent or override compile/reference failures.

Source root resolution follows the host context: explicit --root first,
then the working directory if it contains main, then main's parent. For nested
projects inspect root_resolution and the missing-include warnings. Include
conditionals, repeated includes and source-relative fallbacks use an explicit
conservative project-wide review marker: the anchors are literal locations,
and counts do not claim that TeX executed those objects. Direct-register
occurrences are lexical hints requiring full-context review: \the\itemsep reads
a value, and a custom macro can consume a register as an argument. Such hints
do not certify an executed assignment and may include following body text.

CLI exit 0 only means that a complete PDF measurement was produced; it does
not mean the target is met, the build is current, or the layout is accepted.

## Choose a coherent trial

The six families below are a menu. Choose the cause with the largest plausible
benefit for this document; no fixed order or one-family-per-trial restriction
applies. Related changes can be combined when their effect and interactions can
be explained and verified. Preserve enough real budget for full-paper flow and
final acceptance. PaperFit recommendations ask: **请你看看这是否是对的**;
check current source/PDF evidence and accept, adapt or reject them.

1. Float flow: restrictive H placement, barriers, float tails, native width/span
   and related-figure composition. Keep assets, captions, labels and reading
   order; combining figures must preserve label readability.
2. Local float/caption gaps: inspect actual explicit gaps and native spacing.
   A 1-2pt trial can help a small defect, but is not a required starting size
   when the actual cause is a large erroneous gap. Retain visible separation;
   negative space must not overlap elements.
3. Template spacing: adjust one display/list/paragraph glue family within
   template rules. Preserve stretch/shrink and body font. Inspect actual output:
   a preamble value may be overridden by a class or list environment.
4. Native reflow: review breaks, widow/orphan settings and equation operators.
   A local paragraph setting must stay active through the paragraph end, e.g.
   {\looseness=-1 <same paragraph>\par}. TeX may not honor the request. Preserve
   all formula operands, signs and labels.
5. Table spacing: readable row/column padding or template-permitted local table
   type. Keep ordered cells, units, values and notes. Never shrink subscripts
   to unreadability by whole-table scaling.
6. Equivalent rearrangement: combine related layout corrections and check
   their interactions in the compiled candidate. Prose paraphrases require authorized,
   information-equivalent edits and independent scientific review. Never add
   filler or remove sentences/information.

Under-target papers need natural spacing/readability or float-flow restoration,
not padding or invented content. Equality can still leave a sparse poor last
page: inspect it and continue improving the layout.

## The last page and validation loop

Use the inventory to find repeated display/list/float gaps, breaks and table
structures. One saved line or a 1-2pt gap is a micro trial, not a guaranteed
0.1-page saving. Measure actual PDFs; text bounds alone cannot measure blank
space around images, vectors or floats.

Keep source/assets/PDF checkpoints. After a trial, check content for every
changed source, including includes. Known layout parameters should pass;
scientific numbers, symbols, data, ordinary prose, hidden content and body font
changes stay protected. Distinguish a confirmed content change from a parser limitation instead of
disabling guards. Unknown constructs need independent review with retained
raw guard evidence.

Compile and rerender each coherent candidate with the original engine. Actually
inspect every physical page before accepting it or choosing the next repair,
checking changed objects at reading zoom and document-wide flow against useful
checkpoints. Auxiliary API vision may supplement the host; full-document visual
inspection is required for each candidate. Record the complete inspected page
set and the retention/revision/rollback decision. A candidate may contain a
batch of related edits. Keep verified progress and continue despite unchanged
original defects or a still-unmet page objective; intermediate retention is
separate from final completion. If one edit regresses, correct or selectively
revert it while preserving independent successful fixes, then rebuild, rerender
and inspect every page of the resulting combined candidate. Use the smallest
safe checkpoint if interacting edits cannot be separated reliably. Inspect every
final physical page after the last source change. Exact misses cannot be labelled
final success. Continue with the host within the authorized task/protocol budget;
an optional batch round limit does not establish goal completion. If a real
resource or budget limit prevents completion, explicitly report unfinished.

After a patch/helper failure, first establish whether any source bytes changed
and restore or correct a partial edit. A no-change preparation failure is a tool
failure rather than a new measured layout candidate. An experimental protocol
may count reservations differently; retain that declared accounting and disclose
the distinction rather than modifying historical budgets.

Record the next trial at a new evidence path (`--attempt-tier` identifies an
intervention family for reporting and imposes no ordering):

    paperfit page-fit --main paper.tex --target-pages 12 --page-policy exact --before-pdf checkpoints/previous.pdf --attempt-tier 3 --defects-stable --output .paperfit/reports/page_fit_02.json

This records identities and measured differences, never automatic acceptance.
Also retain source diffs, guard reports, compiler logs, page images, actual
host reviews, keep/rollback decision and reason. Compare with both the predecessor
and a useful retained candidate so later edits cannot erase an achieved objective.

## Reproduction limits

These tools do not establish official scores. Record actual host, available
model information, tool versions, input hashes, budget, calls and costs.
Never reuse old scores for a changed source identity or treat synthetic
regressions as wins on the fixed benchmark.

## PDFs from isolated builds

A host may keep compiler output outside the manuscript to preserve its input
files and previous candidates. Selecting that PDF with `--pdf` exposes its
measurements; selection alone does not bind it to the current source.

For a new recorded build, choose an unused directory outside the paper:

    paperfit isolated-build --main paper.tex --root . --build-dir ../build-trial-01 --engine pdflatex
    paperfit page-fit --main paper.tex --root . --build-dir ../build-trial-01 --target-pages 12 --page-policy exact --output .paperfit/reports/page_fit_trial_01.json
    paperfit run scripts/host_context.py --main paper.tex --root . --build-dir ../build-trial-01 --output .paperfit/reports/host_context_trial_01.json

The build command copies ordinary nonhidden project inputs, including local
styles, bibliography, figures and scientific data directories. Its inventory
excludes environment directories and generated TeX suffixes, listed in the
receipt. Keep the compilation root as the complete paper project. Absolute
external includes and system TeX dependency identity are outside this binding
scope; a local receipt is not independent authentication of the compiler.

Relative input lookup defaults to the main TeX file's directory. For a project
that intentionally resolves inputs at its root, pass
`--working-directory project_root`. The receipt retains that choice; it cannot
silently substitute a same-named include from another directory.

With only `--build-dir`, both analysis tools select that build's
`build/<main-stem>.pdf`. An explicitly supplied `--pdf` must match the selected
build. Store new reports under `.paperfit/reports/` or outside the live and
copied source directories. Ordinary `data/` remains part of the scientific
input set; writing a report there would invalidate the binding just measured.
Existing source, PDF and evidence files are never replaced by these reports.

The page-fit tool rechecks current input hashes against the copied snapshot,
compiler outcome, selected PDF and retained log/output hashes. A changed include,
style, bibliography or asset makes the objective unknown even if the PDF still
has the requested page count. An explicitly invalid build cannot borrow the
neighboring PDF's clearance. No prior build directory is overwritten.

Render and inspect the relevant pages of a trial, run content and template
checks, then decide whether to keep it. Inspect every physical page before
accepting a final PDF or submitting a review that requires full-page evidence. A verified build/page count does
not establish page views, visible preservation or acceptable layout. Only after
this review should the host assert `--defects-stable` for page-fit trials.
