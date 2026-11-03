# PaperFit in Codex, Claude Code, and Cursor

PaperFit implements a structured vision-in-the-loop workflow in the multimodal host you already use. PaperFit organizes visual diagnosis, constrained source repair and checklist verification; the host executes this workflow by reading actual page images, locating LaTeX objects, editing source and inspecting freshly rendered candidates. Visual feedback is required for diagnosis and candidate acceptance. An auxiliary vision API or separate agent can supplement this loop. Your host model, credentials, permissions, and configuration stay under your control.

## Install the discoverable entry

This workflow is available on this Git branch and has not been published to npm. Use the source checkout and install its dependencies:

```bash
cd "<checkout>"
npm ci --ignore-scripts
node scripts/install-host-global.js --target codex --scope project --project "<paper>"
```

Run tools from the paper directory as `node "<checkout>/bin/paperfit.js" ...`, equivalent to `paperfit ...` below. Optionally run `npm install -g .` inside this checkout to expose that local version's CLI. Omitting `--scope project` selects global scope.

Replace `codex` with `claude`, `cursor`, or `all` for another host. `--project "<directory>"` selects an explicit paper directory. Project scope writes skills/rules and a `.paperfit/install-manifest.json` receipt only in that project. It does not install global assets. Existing files are skipped; `--force` refreshes selected PaperFit assets. The installer does not write `~/.codex/config.toml`, `~/.codex/AGENTS.md`, or a personal plugin marketplace. A receipt version (from the installed package) does not prove existing skill text was upgraded: without `--force`, existing assets are skipped, and doctor checks metadata rather than full text equality. Use `--force` only to refresh the selected PaperFit assets; it does not overwrite unrelated user configuration.

Start a new host session after installing. Codex discovers the entry in `~/.agents/skills/paperfit/SKILL.md` or `<paper>/.agents/skills/paperfit/SKILL.md`. Claude uses `.claude/skills`; Cursor uses `.cursor/skills` and its project rule.

Ask:

```text
$paperfit Inspect this paper's layout and show the important defects.
$paperfit Fix the overflowing figures and tables. Preserve all text, data, captions and labels.
```

In Claude Code, use `/paperfit` or a natural-language request; in Cursor, ask to use PaperFit. A custom Codex `paperfit` role is optional and requires an explicit role declaration in Codex config. The skill workflow works without it. `/agent` switches an existing Codex thread; it does not register PaperFit.

## What the host does

### Editing authority comes from the user

An authorized layout repair permits the current host to use its native editing
tools on related project sources, includes, local style/class files and
configuration within the user's scope. The host can combine interventions,
reorganize related files, or use alternatives outside the taxonomy and tool
menu. An analysis-only request still stops at analysis. Do not ask again for
routine authorized edits or require acknowledgement of every recommendation.
Ask only when necessary intent or authority cannot be established.

PaperFit findings are hypotheses. The host may reject a false diagnosis,
correct its source anchor or change the plan after inspecting actual evidence.
Unknown source constructs call for host investigation, not automatic rollback
or waiting for another human. A definite scientific-content loss, violation of
user template requirements, hidden content, or stale evidence must be corrected;
an unsupported parser cannot establish that such damage occurred or that it did
not occur. Do not silently override a failure to manufacture a passing report.

`host_guidance` describes this authority and the native handoff. Legacy
`human_review` and `approval_required` fields describe an optional batch's
ability to execute a particular operation. That batch retains its scope and
approval checks; it is not a global restriction on the host's editing tools.
When a batch cannot proceed, use its evidence to perform an independently
anchored repair rather than treating its tool coverage as the end of the task.

### Verify coherent candidates

The sequence below is a reusable workflow, not a mandatory command ritual after
every intermediate edit. Group related changes into one coherent candidate,
preserve a checkpoint, compile and rerender that candidate afresh, then actually
inspect every physical page before accepting it or choosing the next repair.
Use source checks that address the actual changes and keep
consequential judgments. Final delivery requires a fresh successful build,
inspection of every physical page and honest reporting of any unmet objective.
Separate API vision is optional. Formal review bindings below are required
when asking a supported guarded route to accept a reviewed operation; routine
native editing itself does not require those route approvals.

A coherent candidate is one reviewable source state after a related edit or a
batch, not the completion of every repair in the manuscript. Finish the planned
batch, then render and inspect all pages; intermediate trial builds can isolate
risky edits. Inspect every page at a readable scale for document-wide changes,
with detailed zoom on repaired objects, shifted floats and suspected regressions.
Coverage is mandatory; identical inspection depth on every page is not.

Keep verified intermediate progress and continue when preservation/template
checks pass and no confirmed regression was introduced. Unchanged original
defects, an ineffective sub-edit, or a still-unmet page objective do not require
discarding independent successful repairs. Record resolved, residual and
new/worsened defects separately, and keep the remaining repair queue. If one edit
causes damage, correct or selectively revert it where separation is reliable,
then rebuild, rerender and inspect all pages of the resulting combined candidate.
If interacting changes cannot be separated safely, restore the smallest safe
checkpoint; keep improvements from previously verified rounds. Intermediate
`keep_and_continue` and final `done` are distinct host judgments, not new CLI
statuses.

1. Find the actual main file, template, includes and requested page scope. Plan readability, visible preservation and page count together; identify broken template overrides and large flow obstructions early.
2. Run `paperfit check-visual --main "<root.tex>"`, then `paperfit run scripts/host_context.py --main "<root.tex>" --output .paperfit/reports/host_context.json`. Use page-fit source opportunities early when a page target applies; a current PDF is required to accept that target.
3. Open actual page images and locate the real object definition. Treat recommendations as hypotheses: **请你看看这是否是对的**. Check evidence and uncertainty, then accept, adapt or reject the advice. An executable operation or automatic PASS is not an overall quality judgment.
4. For an authorized repair, make a coherent source patch with normal host tools, including related multi-file/style changes when needed. Related placement, wrapping and spacing changes may be combined; no specialist order or one-family-per-trial rule applies. Preserve academic content and the user's native template constraints.
5. Compile and rerender each coherent candidate with the original engine. Inspect every current physical page before accepting the candidate or selecting the next repair, comparing target objects and global flow with useful checkpoints. Record actual visual observations and the acceptance checklist. Separate API critique may supplement the host; full-document visual inspection by the host is required for every candidate, without requiring API calls after intermediate keystrokes.
6. Recover from a failed patch before moving on: inspect actual byte changes, correct/restore partial edits, and distinguish no-change helper failures from measured candidates. Keep any separately declared experimental reservation accounting explicit. Inspect every final page before delivery and report an unmet page/readability target.

The optional `paperfit fix-layout --main "<root.tex>" --apply --max-rounds 3` is a guarded automated batch. Without `--apply`, it produces diagnostics/plans. Unsupported automated candidates can still be handled by the host using the same evidence and user authorization. A machine `DONE` or zero findings cannot replace looking at the final page images.

In the phased batch route, supported local edits are grouped and layout/global
edits receive separate trial compiles. After the bounded round of edits, the
runtime compiles and renders the combined state for observation. These trials
are measured safety checks, not host visual acceptance. Round failures with
usable edit records first attempt candidate-level recovery and recheck the gate;
inseparable local batches, absent attribution data or unresolved failures can
still require restoring the round checkpoint. Successful partial recovery may
end the automated run while keeping other repairs for the host to inspect and
continue. The route's actual thresholds are unchanged.

Nested entry files are supported: `--main "paper/main.tex"` compiles in `paper/` and keeps the PDF and native log beside that entry file. Runtime evidence and phased acceptance use the same project-relative nested paths. Final rendering must contain exactly one numerically numbered image for every PDF page, from `page_001.png` through the last page; missing, duplicate, invalid, or extra page numbers prevent terminal acceptance. Each page image must decode successfully with positive dimensions. A filename, nonempty bytes, or a PNG header alone cannot establish rendered-page evidence. An unreadable PDF cannot establish complete coverage.

`paperfit status` rechecks current source and visual artifacts against the checked run, including content hashes. A later host edit or replaced/deleted artifact changes effective `DONE` to `EVALUATING` with `revalidation_required`; the prior gatekeeper decision remains historical evidence. Run fresh diagnostics and inspect all final page images after the last source edit.

Optional API vision can add evidence; it does not replace the host. Incomplete VLM coverage requires host image inspection, and unavailable API vision does not prevent host repairs. Read [DEEPSEEK_VISION_LAYOUT.md](DEEPSEEK_VISION_LAYOUT.md) only when configuring that service.

## Compare preserved candidates before nomination

Keep each useful source/PDF checkpoint. Improving a table can also add a sparse final page, so the latest compiled candidate is not automatically preferable. Compare two preserved PDFs with:

```text
paperfit run scripts/candidate_compare.py --before-pdf before/main.pdf --after-pdf after/main.pdf --target-pages 12 --page-policy at_most --output .paperfit/reports/candidate_comparison_02.json
```

Use `exact` only when equality is the actual objective; a shorter paper can satisfy `at_most`. The read-only tool binds measured page counts and raw painted-text bounds to the two actual PDF hashes. Losing a page constraint, increasing raw out-of-page glyphs, or missing measurements requests host review. It never selects, restores or accepts a candidate. Source preservation, source/PDF correspondence, actual page views, readability and internal overlap remain separate checks. Inspect the specific tradeoff and keep the most appropriate verified checkpoint; report any unmet goal. Each output report must use a new path.

The comparison also records distance to the requested page constraint, including
when both candidates miss it. For `exact` this is the absolute page difference;
for `at_most` it is only the excess pages. It adds extracted type-size and terminal
line observations bound to each measured PDF. These observations do not rank
overall quality or reject legitimate small math, figure labels or footnotes.
Inspect the actual objects at normal reading scale and at a useful zoom. Compare
both the preceding candidate and retained useful checkpoints; preserve their
matching sources and assets so the chosen PDF remains reproducible.

For a focused read-only report, run:

```text
paperfit run scripts/pdf_layout_profile.py --pdf build/main.pdf --output .paperfit/reports/layout_profile_02.json
paperfit run scripts/host_context.py --main paper/main.tex --pdf build/main.pdf --output .paperfit/reports/host_context_02.json
```

`--pdf` explicitly selects an isolated build instead of a possibly stale bundled
source-neighbor PDF. Selection does not assert that the PDF came from current
sources: keep the compile manifest and source/PDF hashes. The profile describes
extracted text geometry rather than all painted images/vectors. Its short-line
heuristic only nominates a restricted Latin-word pattern in multi-line text
blocks; no candidate does not prove absence of widows, orphans or sparse pages.
Check the source paragraph, full page and neighboring page before local reflow.
For compact figures, equation breaks or table wrapping, inspect actual label,
operand and cell readability rather than merely retaining source tokens.

## Preserve scientific payload during native edits

Native host edits do not automatically pass the optional batch guard. Before editing, preserve relevant source/assets or use an existing Git baseline without overwriting author edits. Review all changed files, including includes and local styles, as one coherent candidate. For supported TeX comparisons, the following check helps compare protected content with that baseline; do not repeat unchanged checks after every intermediate edit:

```bash
paperfit run scripts/content_integrity_check.py diff "<before.tex>" "<after.tex>" --output .paperfit/reports/content_diff.json
```

Read `scientific_payload.status`, `changed_components`, `regression_reasons`, and `action_required` in the report. A failed protected-payload check requests rollback (`auto_rollback`, exit 3); `manual_review` exits 2. Restore a confirmed content loss or forbidden operation before proceeding.
For an eligible pending candidate, preserve its baseline and compile/render it
so the host can obtain the source and full-page evidence needed for strict review.
A pending candidate is not accepted by that guarded route and cannot become
its DONE. The host should continue investigating it or choose a better repair;
it need not wait for a separate human. The review interface covers enumerated
cases, not arbitrary source equivalence. Use a distinct output path for each file.

Also read the separate `layout_policy.status` and `findings`. New or changed positive figure/table scaling, explicit page breaks, and local spacing can require `manual_review` even when protected content is unchanged. Body font reductions, nonpositive scaling and hiding content remain blocked and cannot be approved. Small local text needs its actual context checked: a legitimate footnote or equation script is different from making a table's measured results unreadable. Existing operations and their removal do not grant permission to introduce another restricted operation. Neither a VLM finding nor a source comment supplies approval.

The executor retains a pending candidate with `status: "manual_review"`, `success: false`, and no rollback, so the host can compile it and inspect it. Confirmed forbidden operations or content loss restore all tracked source bytes. The live gatekeeper checks every tracked TeX include against its verified snapshot; pending evidence produces `CONTINUE`, never `DONE`. This policy check does not establish scientific equivalence, visual quality or a page goal.

Combining two tables in one float should retain two scientific objects. For supported standard groups or minipages with unique labels and literal captions, the checker compares each object's caption, ordered cells, formulas and assets against the same label before comparing container counts. A changed number or caption remains a regression even if the same tokens exist somewhere else in the combined float. Ambiguous labels, redefined caption commands or uncovered float content leave this structural exemption unavailable; preserve that candidate for independent source and page inspection instead of interpreting the parser's uncertainty as proof that the host's idea is wrong.

### Submit a review of the current candidate

Preserve a baseline for every edited TeX file. Compile the candidate through PaperFit with the original engine, then render and actually inspect **every** current page. Requests need the latest successful native compile journal, unchanged before/after source hashes, actual imported project-resource hashes and recorder file, current PDF hash, and the complete decoded page-image set. The supported binding uses the PDF beside the actual main file, including a nested entry such as `paper/main.tex`. An isolated observation build or an old journal without these receipts cannot authorize an operation; it can still supply evidence for the host to inspect. Missing binding leaves a request pending.

Preserve an author-supplied `.bbl` as a real bibliography input. A newly generated bibliography has no pre-compilation hash; the compiler can succeed while the strict source binding remains unknown. Read process success and source-binding status separately. Keep the generated artifact and its provenance, inspect the PDF, and obtain a current complete binding before submitting a native review. Copying an isolated PDF next to the manuscript or constructing a native journal by hand cannot establish that binding.

Generate an unsigned request rather than assembling hashes by hand:

```bash
paperfit run scripts/layout_policy.py request --root "<paper>" --main-tex paper/main.tex --file sections/results.tex --baseline-tex data/baselines/results.before --pages-dir data/pages > data/layout_review_request.json
```

Read the exact operations and binding in the request. Only after checking the source, current template and all page images, the host can explicitly set `approved: true`, a concrete reason, and the requested visual/readability/no-blank-padding confirmations. Do not fabricate or replace binding fields. Validate the signed copy against the same current candidate:

```bash
paperfit run scripts/layout_policy.py validate --root "<paper>" --main-tex paper/main.tex --file sections/results.tex --baseline-tex data/baselines/results.before --pages-dir data/pages --review-json data/layout_review_signed.json
```

Request generation or validation returns 3 for pending evidence and 2 for a hard block; validation returns 0 only for this layout-policy channel. It does not approve content, page count or the final PDF. Any source, imported graphic/style/class, recorder, PDF, compile attempt or page-image change invalidates the binding and requires a fresh request and review.

An unchanged graphic filename does not establish unchanged visible content. New or changed `includegraphics` crop, viewport, page-selection or other content options require review; ordinary positive native sizing stays available. Explicit zero/negative size or scale and draft hiding are blocked for the standard command. Redefined or constructed commands remain opaque. To approve a legitimate crop, inspect the original source asset and all current pages, then fill `source_asset_input` with that asset's exact `{path, sha256}` record from the request's imported-input binding and confirm `source_asset_inspected`, `visible_content_preservation_confirmed` and `readability_confirmed`. These are explicit host judgments; the checks bind evidence and do not prove visual semantics.

Classified source uncertainty has a separate request and validation interface in `scripts/source_review.py` with the same `--root`, `--main-tex`, `--file`, `--baseline-tex`, `--pages-dir` and `--review-json` options. It covers only enumerated finite presentation/anchor uncertainties, including narrowly recognized reference-local font hooks. It requires explicit source, template, content and full-page confirmations and any requested declaration or intended-object evidence. It cannot approve a changed claim, marker, math sign, number or table cell. The effective report keeps the complete automated failure or pending result under `automated_report` and `automated_status`.

For a float wrapper moved across literal includes, a file-local comparison may
lose the parent scope. The live snapshot guard can reconstruct a bounded owning
context, retain the raw failures and classify exact wrapper-only differences as
pending `include_float_scope_uncertainty`. Use the original verified snapshot,
and name that owning parent file rather than submitting separate fragment
approvals:

```bash
paperfit run scripts/source_review.py request --root "<paper>" --main-tex paper/main.tex --file sections/results.tex --rollback-target data/backups/repair_01/snapshot_manifest.json --pages-dir data/pages > data/include_review_request.json
```

`--rollback-target` selects this multi-file route and replaces `--baseline-tex`.
The request reconstructs the literal include graph and exact source identities
from the verified snapshot; it does not trust a caller-supplied claim of equal
content. Compile/render first when obtaining the final request binding. After
actually checking the sources, current template and every page, explicitly
confirm the requested source/content/template/page flags, intended objects and
caption/label bindings, and provide a concrete reason. Preserve all generated
hashes, classification and binding fields. Then validate:

```bash
paperfit run scripts/source_review.py validate --root "<paper>" --main-tex paper/main.tex --file sections/results.tex --rollback-target data/backups/repair_01/snapshot_manifest.json --pages-dir data/pages --review-json data/include_review_confirmed.json
```

Pass that confirmed file explicitly as `--source-review-json` to the live
gatekeeper below. Its science/prose correction applies only to the exact
reviewed include context; ordinary layout-policy approval and final page goals
remain separate. Changed payload, unknown ownership, duplicate requests or
changed source/build/pages cannot inherit the confirmation. This route supports
normal host edits; the optional batch executor does not provide arbitrary
multi-file wrapper relocation or apply this context review in its single-file
integrity adapter.

Pass each signed file explicitly to the executor or live gatekeeper:

```bash
paperfit run scripts/repair_plan_executor.py data/repair_plan.json paper/main.tex --root "<paper>" --output .paperfit/reports/repair_execution.json --layout-review-json data/layout_review_signed.json --source-review-json data/source_review_signed.json
paperfit run scripts/gatekeeper_enforcer.py --root "<paper>" --state data/state.json --layout-review-json data/layout_review_signed.json --source-review-json data/source_review_signed.json
```

Omit the source-review flag when no classified source uncertainty needs review; omit the layout-review flag when no restricted layout operation changed. Repeat either flag for separate changed files. Each request must identify the actual task main and an unambiguous file; layout requests must use one consistent compile/page context. Duplicated keys, malformed contexts and a different task main fail before execution. The legacy `--layout-policy-approvals` interface remains accepted for compatibility, but weak file/line/argument or arbitrary image-string evidence lacks current binding and cannot authorize a candidate. API callers supply `project_root`, `review_context`, `layout_policy_approvals` and `source_reviews` explicitly. Read the report's `status`, `success`, channels and gatekeeper `decision`; successfully writing a report does not mean the paper is accepted. Neither path auto-generates approval or takes over the host's repair work.

The word-based `semantic_hash` filters math and command arguments and cannot alone establish scientific preservation. The independent payload gate detects drift in symbols, numbers, subscripts/superscripts, fraction structure, equation-label binding, ordered table cells, and labels/references/citations while allowing supported layout breaks and sizes. It compares only the supplied source; it does not expand macros, prove algebraic equivalence, or prove complete visible-content preservation. Independently review unknown macros, dynamic TeX, and complex rewrites, then compile and inspect the result. A protected-token difference means the automated gate cannot establish safety, not that scientific damage is proved. Equivalent structural rewrites, such as splitting a table into panels, require independent comparison of data/formulas and the full PDF; do not force a passing result without that evidence.

`scientific_payload.assessment` distinguishes supported preservation, a changed
protected token, unsupported constructs needing review, and checker errors. It
never claims complete semantic proof. Standard delimiter sizes, invisible
cross-line delimiter continuations and literal `parbox` layout arguments are
supported; redefining those commands makes their meaning uncertain again.
Known layout-length registers accept both standard first-argument spellings,
such as `\setlength{\intextsep}{10pt}` and `\setlength\intextsep{10pt}`,
and literal `\addtolength` dimensions. Dynamic values, unknown registers and
direct redefinitions retain their payload. Balanced standard
`\begingroup ... \endgroup` scopes preserve their ordinary body tokens during
local reflow; incomplete/redefined scopes remain opaque. These normalizations
also retain scopes containing definitions, `\let`, `\global`, `\aftergroup`
or unknown commands, because their effects can depend on grouping. They
do not approve restricted spacing/template changes or weaken claim, number or
formula-sign preservation. Compile and inspect every resulting page.

Standard `rotating` floats, including `sidewaystable*` and `sidewaysfigure*`,
retain their caption, label, table-cell, graphic-asset and mathematical payload
when compared with the corresponding ordinary float. Rotation itself does not
exempt changed numbers, signs or labels. This source recognition does not
approve rotation for a venue: inspect the actual template, readable complete
content, neighboring pages and page objective after compiling.

If a perturbation activated a missing graphic from an entirely commented-out
figure, native `diff` has a narrow restoration path:
`--source-restoration-evidence <JSON>`. Evidence binds the before/after source
hashes, an actual local reference-source archive/member and recorded provenance;
the restored line must match the reference's exact comment. The literal asset
must actually be absent within the project, and the surrounding figure, caption
and label must already be commented. Only that validated removed-asset count is
reconciled; raw inventory and all other structure/science/prose/template checks
remain. This is not an arbitrary figure-deletion approval or network-authentication
claim. The `post` command does not accept this restoration evidence.

## Physical-page text visibility

`host_context.py` now rechecks painted glyph positions in the live PDF, including
characters beyond the page that accessible `ActualText` can hide from ordinary
text extraction. `pdf_visibility` records the PDF hash, full-document coverage,
page boxes, glyph bounds and measured overflow. It checks text geometry, not
mathematical equivalence or all image/vector content. Empty VLM findings cannot
cancel measured out-of-page text; a pending finding requires host inspection
before `DONE`, without automatically rolling back the source.

The source navigation brief may find a neighboring bundled PDF. Its geometry
alone does not establish that it was built from the current source. The brief
marks such a result `unverified_current_source`, retains its raw
`geometry_status`/`geometry_pass`, and reports the actual PDF hash. Current
applicability requires matching main/source-set/PDF identities in the runtime
snapshot and a current source timestamp check; it does not require a successful
VLM response. For an isolated build directory, inspect the explicitly selected
fresh PDF with `pdf_visibility_check.py`; do not substitute a source-neighbor
PDF for that build.

An embedded figure can deliberately crop a label. Inspect the complete page
and source asset before deciding. For such an intentional crop only, save
`data/pdf_visibility_reviews.json` with schema `paperfit_visibility_reviews.v1`,
the current `pdf_sha256`, and a `reviews` list. Each review must have the exact
`finding_id`, `disposition: "intentional_clipping"`, a concrete `reason`, and
`inspected_page_image` containing project-relative PNG `path`, physical `page`,
`sha256` and integer `render_dpi` (36–300). Render that full page with PyMuPDF
at the recorded DPI and `alpha=False`. The checker decodes the image and compares
its pixels against a fresh render of that PDF page; stale or unrelated images
cannot satisfy review. Recompile or change the inspected image and review must
be renewed. Scientific payload, prose and template checks still apply.

For standalone aggregation with a nested main file, pass `--project-root` to
identify the compilation root. The runtime supplies it automatically. Unavailable
geometry is reported explicitly; do not claim it proves complete visible content.

## Whole-document B1 approval

B1 moves floats without changing image dimensions; resizing uses typed B2. In the optional typed planner, an unanchored B1 remains `human_review`, while a precise label can support a local placement repair. A VLM page finding alone cannot authorize that tool's global `[p]` or float-parameter changes. The host can independently inspect and perform an authorized whole-document flow repair with native tools; the typed approval requirements below apply when requesting that planner's document-wide route.

For a host-reviewed whole-document candidate, pass independent approvals to `build_visual_action_plan`:

```python
document_scope_approvals = {
    "finding-123": {
        "document_scope_approved": True,
        "hard_guard_evidence": {"type": "float_stacking", "pages": [5]},
        "endmatter_float_intrusion": False,
    }
}
build_visual_action_plan(..., document_scope_approvals=document_scope_approvals)
```

The approval must be exact `True` with structured evidence; finding/metrics/VLM fields cannot supply it. Supported evidence types include `float_stacking`, `float_only_pages`, `endmatter_float_intrusion`, and `body_float_intrudes_endmatter`. A structured available hard-guard report with nonempty intrusions and a hard-failures list is also accepted. A `clearpage` change additionally requires structured endmatter evidence or explicit `endmatter_float_intrusion: True`; stacking evidence alone is insufficient.

## Tool paths and verification

`paperfit root` prints the package root. Package scripts and detailed layout references live there, not in the user's paper. In a source checkout, `node "<checkout>/bin/paperfit.js" ...` is equivalent to `paperfit ...` and keeps the paper working directory. If the command is absent, the install receipt's `packageRoot` provides this path.

Run `paperfit doctor --target codex --project "<paper>"` to check the toolchain and actual discoverable skill metadata. It reports failures and exits nonzero; missing optional custom roles/legacy prompts/marketplaces are not errors.

For native plugin distribution, the repository/npm package includes portable root `plugin.json` and `skills/`. The repo marketplace points to that root. Adding a marketplace makes it discoverable; installation/enabling still follows the host's plugin flow. The explicit skill installer above is sufficient for CLI use.

Official references: [Cursor skills](https://prod.cursor.com/help/customization/skills), [Claude skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [Codex skill discovery](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills), [plugin packaging](https://developers.openai.com/plugins/build/plugins), and [custom role configuration](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml).


## Native template spacing parameters

A finite positive literal override of a zero-argument template spacing macro can be classified as pending host review; it is never an automatic scientific PASS. The source-review `request` command suggests `template_parameter_evidence` from current compiled project `.cls`/`.sty` inputs, with `macro`, relative `file`, `sha256`, `declaration_line`, `consumer_line`, and `template_checked: false`. Inspect the indicated native declaration and direct length-register consumer, select one exact evidence record per macro, and set its confirmation only after checking the source and all current compiled pages. This typed evidence is separate from `declaration_evidence`.

Use the existing `source_review.py validate --root ... --file ... --main-tex ... --baseline-tex ... --review-json ...` command before passing the review to an executor. Validation also requires the current successful compile journal, imported-input hashes, PDF and complete page images, plus explicit source/template/content/all-page confirmations and a reason. Stale or missing evidence remains pending; aliases and scientific literal uses are rejected. Verified host review preserves the raw automated failure and cannot waive body-font policy, content loss, or final page/layout acceptance.
