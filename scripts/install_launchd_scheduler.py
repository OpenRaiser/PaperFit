#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate launchd plist from PaperFit template")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--automation-hook", default="")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    template = Path(args.template).resolve()
    output = Path(args.output).resolve()
    script_path = (repo_root / "scripts" / "scheduled_run.sh").resolve()
    log_dir = repo_root / "logs" / "scheduler"
    log_dir.mkdir(parents=True, exist_ok=True)

    content = template.read_text(encoding="utf-8")
    content = (
        content.replace("__SCRIPT_PATH__", str(script_path))
        .replace("__REPO_ROOT__", str(repo_root))
        .replace("__AUTOMATION_HOOK__", args.automation_hook)
        .replace("__START_INTERVAL__", str(args.interval))
        .replace("__STDOUT_PATH__", str((log_dir / "launchd.stdout.log").resolve()))
        .replace("__STDERR_PATH__", str((log_dir / "launchd.stderr.log").resolve()))
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
