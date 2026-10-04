# PaperFit Commands Setup

## Scope

This document describes the Claude-side command assets installed by PaperFit and how users should interact with them after installation.

## Installed Command Assets

```text
PaperFit/
└── .claude/
    ├── CLAUDE.md
    ├── settings.json
    └── commands/
        ├── paperfit.md
        ├── fix-layout.md
        ├── check-visual.md
        ├── repair-table.md
        ├── adjust-length.md
        ├── migrate-template.md
        ├── show-status.md
        ├── paperfit-priority.md
        └── paperfit-undo.md
```

## Product Guidance

- The primary PaperFit experience is **natural-language-first**.
- Users should describe goals directly, for example:
  - `/paperfit Analyze this paper's layout`
  - `/paperfit Repair the layout issues in this project`
  - `/paperfit Migrate this paper to CVPR`
  - `Use PaperFit to inspect the visual layout before making changes`
- Expert shortcut commands remain available, but they are not the only valid entry path.
- Internal commands such as `paperfit run`, `paperfit runtime`, and `paperfit render` are execution details for the agent, not the main user interface.

## Available Claude Commands

| Command | Role |
|------|------|
| `/paperfit` | Unified natural-language entry for analysis, repair, migration, length adjustment, table repair, status, and undo routing |
| `/fix-layout` | Expert shortcut for the full VTO closed loop |
| `/check-visual` | Expert shortcut for visual-only inspection |
| `/repair-table` | Expert shortcut for table-focused repair |
| `/adjust-length` | Expert shortcut for page-budget adjustment |
| `/migrate-template` | Expert shortcut for template migration |
| `/show-status` | Status and summary query |
| `/paperfit-priority` | Priority explanation and override |
| `/paperfit-undo` | Roll back the latest automatic write |

## How To Use In Claude Code

1. Restart Claude Code or reload the window after installation.
2. Open the paper project root.
3. Prefer a natural-language request through `/paperfit` or a direct natural-language prompt.
4. Use expert shortcut commands only when you want to force a specific PaperFit task type.

Examples:

```text
/paperfit Analyze the layout of aaai24_antibody.tex
/paperfit Repair this paper's layout with minimal semantic changes
/fix-layout --target-pages 9 --strict
/check-visual
```

## Operational Notes

- Post-edit hooks may auto-compile `.tex` files depending on the installed Claude settings.
- Stop hooks may print a state summary at session end depending on the installed Claude settings.
- If a command is not visible after install, confirm you are in the correct project directory and reload Claude Code.
- If execution fails, check Claude permissions and confirm Poppler and LaTeX are installed.

## Recommended Verification

After installation, verify the product path with one of these:

```text
/paperfit Analyze this paper's layout
/show-status
```

If those work, the Claude-side PaperFit entrypoints are correctly installed.
