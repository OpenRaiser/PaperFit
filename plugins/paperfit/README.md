# PaperFit for Codex

PaperFit adds the `paperfit` layout skill to the host. Install the CLI, then run `paperfit-install --target codex`, or `paperfit-install --target codex --scope project` from a paper project. Open a new Codex session and use `$paperfit` or a natural-language PaperFit request.

The host inspects rendered pages, edits LaTeX, and verifies the PDF. CLI scripts are tools; a separate agent or custom slash-prompt registration is optional. The `commands/` files are legacy workflow references, not registered Codex slash commands.
