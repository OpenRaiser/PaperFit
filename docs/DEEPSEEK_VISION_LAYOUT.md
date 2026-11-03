# DeepSeek page inspection with PaperFit

PaperFit remains the layout agent used by Claude Code, Codex, or Cursor. Your
host agent plans the work and uses PaperFit's compile, inspect, patch, and
verification loop. DeepSeek is an optional image inspector in that loop; it
does not replace the host model or configure its provider.

## Configure the image inspector

Set `DEEPSEEK_API_KEY` in the environment inherited by the host agent or by
PaperFit. The default endpoint is `https://api.deepseek.com` and the default
vision model is `deepseek-flash`. Keep real keys out of source files, committed
configuration, command logs, and shared reports.

For another OpenAI-compatible image service, set all three explicitly:

```text
PAPERFIT_VLM_API_KEY=<your service key>
PAPERFIT_VLM_API_URL=https://your-service.example/v1
PAPERFIT_VLM_MODEL=<image-capable model name>
```

Existing `BASELINE_VLM_API_KEY`, `BASELINE_VLM_API_URL`, and
`BASELINE_VLM_MODEL_NAME` configurations continue to work. A complete legacy
configuration takes precedence over `DEEPSEEK_API_KEY`. PaperFit-specific
settings take precedence over both. The diagnosis client uses the Python
standard library; it does not require an OpenAI SDK installation.

If the host has multiple Python installations, `PAPERFIT_PYTHON` can point to
the Python executable containing PaperFit's dependencies. The CLI also detects
the repository's `.venv` automatically.

## Ask the host agent to repair a paper

From the paper project directory, ask:

```text
Use PaperFit to repair this paper's visible layout defects. Keep the academic
content, all figures and tables, and the template fonts. Inspect the before
and after PDF pages and report remaining issues.
```

The host can inspect first with `paperfit check-visual --main main.tex`, then
execute the existing repair loop with
`paperfit fix-layout --main main.tex --apply --max-rounds 3`.
The `--apply` flag authorizes source changes. Use a copy or a version-controlled
paper project so the resulting source diff is reviewable. Do not impose a page
limit merely to make the comparison look better.

## What changes in this version

- Pages are inspected in numeric page order, in small batches with adjacent
  context. Physical PDF page numbers remain explicit in every image mapping.
- Double-column pages include column crops so small formula and table details
  remain visible. Full-page views retain the page layout context.
- Findings retain the printed figure or table number and object kind. A figure
  and table sharing a number cannot silently anchor to the wrong source object.
- A figure-width finding carries `overflow_width` or `underfilled_width` when
  the image evidence supports that distinction. The type survives planning and
  execution; an untyped model observation stays available for host review.
- Width repair reads the last effective graphics width option, including images
  inside captions. A confirmed undersized figure can expand its sole body image
  while leaving caption images alone; multiple body panels remain unresolved.
- Malformed, truncated, failed, or incomplete inspection cannot set `clean: true`.
  With no validated findings its result is unknown (`clean: null`); with valid
  findings it is already known to be unclean (`clean: false`). A completed
  inspection with no visible findings has `clean: true`. Contradictory error
  fields and duplicate JSON fields are rejected.
- `data/visual_signal_report.json` records inspection status, coverage, missing
  pages, batches, and usage. A configured incomplete inspection cannot support
  a final runtime `DONE` claim. CV and log evidence remain available for repair.
- Quantified table overflow first reduces only the necessary local cell padding,
  preserving font size and numeric columns. Unsafe guesses remain unresolved
  for the host agent to review.

`clean: true` means the model reported no defects in the inspected images. It
does not certify conference compliance or replace the host's final page review.
Treat individual findings as observations, not proven defects. If a model claims
formula overflow but the rendered equation fits inside the margin, the host
should review the image and log evidence before proposing another source edit.

## Control detail and API work

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPERFIT_VLM_PAGES_PER_BATCH` | 2 | Full pages in each request |
| `PAPERFIT_VLM_BATCH_OVERLAP` | 1 | Adjacent pages repeated for context |
| `PAPERFIT_VLM_MAX_IMAGE_DIM` | 2400 | Maximum long edge per image |
| `PAPERFIT_VLM_DETAIL_CROPS` | auto | Enabled for double-column papers |
| `PAPERFIT_VLM_MAX_BATCHES` | unlimited | Cap requests; capped coverage is partial |
| `PAPERFIT_VLM_TIMEOUT_SEC` | 240 | Timeout for one request |
| `PAPERFIT_VLM_MAX_TOKENS` | 4096 | Maximum response tokens per request |

Page inspection runs before and after repair, so a repair run can make several
passes over the pages. A request cap is an explicit tradeoff: incomplete coverage
must be reviewed or retried before reporting the document as finished.

HTTP 401/402, or HTTP 403 with a recognized authentication/permission error code,
stops subsequent batch requests; validated earlier pages and findings remain in
the `partial` or `failed` report, with every unseen page recorded as missing.
An unknown HTTP 403 fails only its batch and permits later batches to run; errors
never authorize switching services or models, and raw response bodies are not logged.

Protocol reference: [DeepSeek Vision documentation](https://api-docs.deepseek.com/guides/vision/).
