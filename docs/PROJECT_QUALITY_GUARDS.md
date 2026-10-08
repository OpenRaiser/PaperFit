# Layout quality checks and their limits

PaperFit runs inside a host such as Codex. The host can already edit LaTeX,
compile it, and inspect images. PaperFit's contribution is reusable source
localization, guarded repair execution, evidence freshness, and acceptance
checks. These features do not establish that an unrestricted host cannot do
the same work, or that PaperFit has the best result on every document.

The user's request determines the host's editing authority. PaperFit's tools
and supported repair families are capabilities, not a ceiling on native host
edits. An authorized host can change related source, includes, local styles or
classes, combine interventions and use independently verified alternatives.
The optional batch's `human_review` or `approval_required` status asks for
investigation or a different route; it does not require another human for
routine authorized work. The host can accept, adapt or reject advice and should
record consequential judgments without acknowledging every detector finding.

## Separate evidence from judgment

Content checks, layout policy, and visual quality answer different questions:

| Channel | What a successful check establishes | What it does not establish |
|---|---|---|
| Scientific payload | Supported formulas, numeric tokens, ordered table cells and references remain intact | Algebraic or semantic equivalence of arbitrary TeX rewrites |
| Layout prose | Ordered ordinary-body tokens remain intact under supported presentation controls | That an intentional prose rewrite preserves meaning |
| Layout policy | No newly introduced operations violate the declared restricted layout policy | That every operation outside that policy is intrinsically ugly |
| Render coverage/readability | Every physical PDF page has a readable numbered image | That a readable image is visually good |
| Freshness | Evidence still belongs to the checked source and artifacts | That the checked layout is optimal |

The layout-policy channel classifies new or changed positive table/graphic
`resizebox`/`scalebox`, forced page breaks and local spacing as `manual_review`.
These operations are not rejected solely by command name. Supported review
routes can accept them after the host checks their exact current context,
readability, content and template requirements; the route's compile/source/PDF/
page binding still applies. Opaque operations may not be covered by that review
interface. Body-font reduction contrary to the native template, hiding content
and nonpositive scaling remain blocked. Existing operations are preserved,
removal is allowed, and normal `includegraphics` width changes, table-column
reflow, equation breaks and scoped paragraph adjustments remain available.
Unknown TeX and absent evidence require host investigation; the report must not
describe uncertainty as proven content damage or silently make it PASS.

Intentional rewriting needs its own authorization and review. A layout
candidate label cannot authorize a hidden change from “does not improve” to
“does improve”. Source comments and VLM findings cannot approve their own
restricted operations. Native host edits are compared with preserved source
copies, and the runtime checks tracked includes against a verified snapshot.
Verification follows each coherent candidate rather than every intermediate
edit. Use relevant source checks and inspect affected/neighboring pages during
iteration; obtain a fresh successful build and inspect all final physical pages
before accepting a result.

## Decide whether a repair is better

Compare candidates using the same document, engine, dependencies and requested
page scope. Check the target object and neighboring pages, then all final
pages. Measure defects with source anchors, log warnings, PDF geometry or line
counts where appropriate. Also inspect readability, cropping, spacing and
template compliance. A lower warning count or correct page count alone is
insufficient. An unchanged render means a purported visual repair has no
demonstrated benefit. Keep the earlier source when a candidate adds a
regression, lacks benefit, or cannot be validated.

VLM findings are advisory. Confirm them against source and rendered pages.
A detector is not the final authority, and absence of detections is not proof of quality.

Focused executable checks cover layout policy, prose integrity, paragraph layout,
rendering and snapshots. Run `python -m unittest discover -s scripts -p "test_*.py"`.
VLM findings are advisory. Confirm them against source and rendered pages.
A detector is not the final authority, and absence of detections is not proof of quality.

Focused executable checks cover layout policy, prose integrity, paragraph layout,
rendering and snapshots. Run `python -m unittest discover -s scripts -p "test_*.py"`.
