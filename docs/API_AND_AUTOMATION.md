# API and Automation

PaperFit does not currently ship as a hosted web API or an HTTP server. It is a local CLI plus host-specific agent assets for Claude Code, Codex, and Cursor.

## Supported Integration Paths

| Path | Status | Use it for |
|------|--------|------------|
| Agent hosts | Supported | Natural-language layout inspection and repair inside Claude Code, Codex, or Cursor. |
| Local CLI | Supported | Shell scripts, CI jobs, Makefiles, Python `subprocess`, or Node `child_process`. |
| Codex provider configuration | Supported indirectly | Using Codex with the user's preferred login, OpenAI-compatible gateway, proxy, or enterprise relay. |
| HTTP/REST API | Not built in | Wrap the `paperfit` CLI in your own service if you need remote calls. |

## CLI Examples

Run these from a LaTeX paper project root:

```bash
paperfit render main.pdf --output data/pages
paperfit run scripts/parse_log.py main.log --output data/log.json
paperfit runtime --state data/state.json run-round main.tex --template ICLR2025 --target-pages 9
```

## Codex Providers

PaperFit does not hard-code `api.openai.com`. Codex owns the model provider, base URL, auth mode, and model choice. See [CODEX_PROVIDER_SETUP.md](CODEX_PROVIDER_SETUP.md) for an OpenAI-compatible gateway example.
