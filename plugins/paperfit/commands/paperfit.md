---
description: Inspect or repair LaTeX paper layout with the PaperFit skill.
---

Use the PaperFit skill for this request: $ARGUMENTS

Read the installed `paperfit` skill, or run `paperfit root` and read `skills/paperfit/SKILL.md` beneath that directory. Follow it in this host session: inspect compiled page images, use normal editing tools for authorized repairs, recompile, and verify the final images. If the request is empty, inspect the current paper and report its layout issues.

A separate agent is optional. Do not require a questionnaire, new API model, or task harness to start. The optional guarded batch `paperfit fix-layout --main "<root.tex>" --apply --max-rounds 3` applies only to repair requests. CLI dry-run findings are not a completed repair.
