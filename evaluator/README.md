# PaperFit Evaluator V3

Standalone snapshot of the current V3 evaluator, packaged on 2026-09-29.
V1/V2 evaluator files, datasets, experiment outputs, caches and credentials
are excluded. Source: `baselines_repro/lib` in the PaperFit project.
The V3 scoring code and prompts are preserved; `common_harness.py` contains
only the PDF page-count and JSON helpers needed by this package.

## Install

Requires Python 3.10+ and Poppler's `pdfinfo` for PDF page counts.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Visual evaluation

Edit the placeholders in `configs/vlm_evaluation.env.example` using your own
OpenAI-compatible vision endpoint, key and model, then export the settings:

```sh
set -a
source configs/vlm_evaluation.env.example
set +a
python evaluate_vlm.py \
  --method-root B0=/path/to/runs/B0 \
  --method-root OURS=/path/to/runs/OURS \
  --methods B0,OURS --evaluate-methods OURS \
  --case-root /path/to/benchmark_cases \
  --sample-size 200 --output-dir results/vlm
```

Input layout for each method root:

```text
<method-root>/<paper-id>/<case-id>/
  reports/evaluation_report.json
  pages/page_001.png
  pages/page_002.png
```

Case metadata is loaded from
`<case-root>/<paper-id>/<case-id>/benchmark_case.json`.
The runner evaluates already-rendered images; it does not compile LaTeX.
B0 is the perturbed input for the left-hand comparison, and the candidate
is on the right. By default, it evaluates 200 cases common to all selected
methods, including cases where B0 could not render (render-rescue mode).
Use `--require-b0-pages` to restrict sampling to successfully rendered B0
cases. Use `--case-list-file cases.txt` for fixed case IDs
(`<paper-id>/<case-id>`, one per line); this explicit list overrides sampling.
Fewer than 200 common cases causes an error rather than a silently smaller
evaluation. Supply `--output-dir` to choose where generated reports are stored.

Outputs include per-case VLM reports, raw responses and comparison images,
plus `vlm_manifest.json`, `vlm_reports.json`, `method_summary.csv` and
`case_scores.csv`. API configuration can also be supplied through CLI flags;
use `python evaluate_vlm.py --help` for all options.

## Program metrics

`lib/evaluators.py:basic_evaluation` accepts existing compile/render reports,
the final source and PDF, and optionally the original case directory:

```python
import sys
from pathlib import Path

sys.path.insert(0, "lib")
from evaluators import basic_evaluation
from common_harness import write_json

report = basic_evaluation(
    method_id="OURS",
    main_tex=Path("/path/to/final/main.tex"),
    pdf_path=Path("/path/to/final/main.pdf"),
    compile_report={"success": True, "tex_log_path": "/path/to/final/main.log"},
    render_report={"success": True},
    case_dir=Path("/path/to/original/case"),
)
write_json(Path("runs/OURS/paper-id/case-id/reports/evaluation_report.json"), report)
```

Populate `success` from actual compile/render results. Metrics include page
budget, log errors, cross-references, source content signals, semantic anchors
and changed lines. Content/semantic checks are source heuristics, not a model
judgment of scientific equivalence. Missing `pdfinfo` produces unknown page
counts; it does not prove the page budget was met.

Aggregate program evaluation reports:

```sh
python aggregate.py --method-root B0=/path/to/runs/B0 \
  --method-root OURS=/path/to/runs/OURS --output-dir results/program
```

The inherited significance helper uses a normal approximation and has a
known inverted p-value branch for constant paired differences. Treat its
significance output as provisional; descriptive summaries are separate.
This snapshot preserves the original implementation for reproducibility.

## V3 prompts and scoring

All current prompts and the response example are in `lib/vlm_protocol.py`:

- `V3_SYSTEM_PROMPT`, `build_v3_user_prompt`: comparison evaluation.
- `V3_RESCUE_SYSTEM_PROMPT`, `build_v3_rescue_user_prompt`: candidate-only
  evaluation when B0 could not render.
- `V3_RESPONSE_EXAMPLE`: expected JSON response structure.
- `normalize_v3_result`: score normalization and constraint gates.

The raw score combines repair absolute quality (35%), pairwise repair (40%)
and final aesthetics (25%). Constraint gates penalize failed rendering,
compilation and other invalid outputs. The `/v1` endpoint suffix in the API
client is the provider API route, not the old evaluator protocol.

## Offline verification

```sh
python -B -m unittest discover -s tests -v
python -B evaluate_vlm.py --help
python -B aggregate.py --help
```

These checks require no API calls. Live VLM evaluation requires your own
configuration and input images. Keep real credentials outside shared archives.
