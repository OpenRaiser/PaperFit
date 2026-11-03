# Review layout candidates with the host

PaperFit supports the host's judgment with source and PDF evidence. A restriction
can be mistaken, and an apparently compact page can be unreadable. Neither a
passing source check nor a vision model's recommendation settles the decision.

Preserve the untouched source and every useful compiled candidate. Record the
engine, complete source/asset inventory, PDF hash and page images. Compare a new
candidate with its predecessor and a useful retained candidate using
`scripts/candidate_compare.py`. Restore a checkpoint only by an explicit host
decision, then verify that its actual source inventory still matches its PDF's
build manifest. A comparison report never selects or restores a candidate.

For example, restoring a template may produce a 12-page candidate. Reformatting
a large table into larger, clearer text may produce 13 pages. The extra page is
a real tradeoff when the requested limit is 12. Inspect table readability and the
new last page, try a better local column/header arrangement if useful, and keep
the verified candidate that best serves the user's request. Do not assume the
last edit is the best one, nor hide an unmet page objective.

`scripts/pdf_layout_profile.py` locates extracted type-size and text-tail
observations for closer inspection. A side-by-side figure may save space while
making labels too small; a nominal font command may not affect the printed
formula until moved outside math. Inspect the actual figure labels, formula
operands and table cells at normal reading scale and at a useful zoom. The tool
does not set a universal minimum font size or certify widows and orphans. Its
restricted Latin-word terminal-line candidates are clues for source and page
review; zero candidates does not prove a clean layout.

When a source guard rejects a change, read its exact channels. Standard ordinary
and starred rotating floats belong to the same figure/table family. Supported
literal paragraph `emergencystretch` settings and standard text `quad`/`qquad`
preserve ordinary tokens, while redefinitions and dynamic constructs remain
uncertain. Changed scientific cells, signs, operands, labels, assets and ordinary
claims still require correction or an independent review with exact evidence.
Recognizing valid syntax does not approve its resulting layout or venue use.

Prefer native equation alignment and operator breaks, local table wrapping and
appropriate subfigure arrangement. Preserve useful inherited wrappers unless
their actual output is defective. The layout-only policy still rejects new or
changed whole-object scaling and ordinary prose rewrites; the host can explain
a legitimate complex transformation with independent source and full-page
evidence rather than applying a global bypass.

After each coherent edit, compile with the same engine, inspect changed and
neighboring pages, and obtain fresh optional visual feedback before the next
decision. Before delivery, inspect all physical pages and report the selected
checkpoint, preserved content, actual improvements and remaining limitations.
See [HOST_NATIVE_WORKFLOW.md](HOST_NATIVE_WORKFLOW.md) for commands and source/PDF
applicability requirements.
