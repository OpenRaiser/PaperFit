# PaperFit for Codex

This plugin adds PaperFit slash commands to Codex.

Install the PaperFit CLI first, then install the Codex host assets:

```bash
npm install -g paperfit-cli
paperfit-install --target codex
```

Equivalent CLI form:

```bash
paperfit install-global --target codex
```

Available commands:

- `/paperfit`
- `/fix-layout`
- `/check-visual`
- `/repair-table`
- `/adjust-length`
- `/migrate-template`
- `/show-status`
- `/paperfit-priority`
- `/paperfit-undo`

Use these commands from the paper project root. You can also ask in natural language, for example:

```text
Use PaperFit to inspect this paper's layout.
```
