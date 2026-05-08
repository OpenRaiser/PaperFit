#!/usr/bin/env python3
"""
终端交互式「论文画像」配置：会刊/模板、页数口径、栏型等，写入 data/paperfit-project.yaml 并初始化 state。
依赖：PyYAML；可选 questionary（无则退回数字菜单 + input）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from template_registry import load_templates as load_templates_with_registry

PAGE_BUDGET_CHOICES: List[Tuple[str, str]] = [
    (
        "main_body",
        "正文主部分（不含参考文献、附录；需与编译产物约定一致）",
    ),
    (
        "with_refs",
        "含参考文献的 PDF 总页数（常见：与页脚页码一致）",
    ),
    (
        "with_appendix",
        "含附录的整份 PDF（附录与正文同一 PDF）",
    ),
]


def package_root() -> Path:
    env = os.environ.get("PAPERFIT_PACKAGE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def load_templates() -> Dict[str, Any]:
    templates = load_templates_with_registry(package_root())
    if not templates:
        path = package_root() / "config" / "templates.yaml"
        print(f"未找到模板文件: {path}", file=sys.stderr)
        sys.exit(1)
    return templates


def template_main_body_pages(meta: Dict[str, Any]) -> Optional[int]:
    pages = meta.get("expected_pages")
    if isinstance(pages, int):
        return pages
    if isinstance(pages, dict):
        main_body = pages.get("main_body")
        if isinstance(main_body, int):
            return main_body
    legacy_target = meta.get("target_pages")
    if isinstance(legacy_target, int):
        return legacy_target
    return None


def banner() -> None:
    lines = [
        "",
        "  PaperFit — 论文版式画像",
        "  ─────────────────────",
        "  会刊/模板、页数口径与栏型将写入 data/paperfit-project.yaml",
        "  并同步初始化 data/state.json（可与 /show-status 对照）",
        "",
    ]
    print("\n".join(lines))


def _numbered_select(options: List[str], prompt: str) -> int:
    print(prompt)
    for i, o in enumerate(options, 1):
        print(f"  [{i}] {o}")
    while True:
        raw = input("请输入序号: ").strip()
        if not raw.isdigit():
            continue
        n = int(raw)
        if 1 <= n <= len(options):
            return n - 1
        print("无效序号，请重试。")


def select_questionary():
    try:
        import questionary  # type: ignore

        return questionary
    except ImportError:
        return None


def ask_text(q: str, default: str = "") -> str:
    qn = select_questionary()
    if qn:
        r = qn.text(q, default=default).ask()
        return (r or default).strip()
    d = f" [{default}]" if default else ""
    raw = input(f"{q}{d}: ").strip()
    return raw or default


def ask_int(q: str, default: int) -> int:
    qn = select_questionary()
    if qn:
        r = qn.text(q, default=str(default)).ask()
        try:
            return int((r or str(default)).strip())
        except ValueError:
            return default
    while True:
        raw = input(f"{q} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("请输入整数。")


def ask_confirm(q: str, default: bool = True) -> bool:
    qn = select_questionary()
    if qn:
        return bool(qn.confirm(q, default=default).ask())
    hint = "Y/n" if default else "y/N"
    raw = input(f"{q} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "t")


def select_from_list(
    title: str,
    choices: List[Tuple[str, str]],
    default_key: Optional[str] = None,
) -> str:
    """choices: (key, label)"""
    qn = select_questionary()
    labels = [c[1] for c in choices]
    keys = [c[0] for c in choices]
    if qn:
        from questionary import Choice  # type: ignore

        qc = [Choice(title=l, value=k) for k, l in choices]
        if default_key is not None:
            r = qn.select(title, choices=qc, default=default_key).ask()
        else:
            r = qn.select(title, choices=qc).ask()
        if r is None:
            sys.exit(0)
        return str(r)
    idx = _numbered_select(labels, title)
    return keys[idx]


def build_venue_choices(templates: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for key in sorted(templates.keys()):
        meta = templates[key] or {}
        name = meta.get("name") or key
        pages = template_main_body_pages(meta)
        col = meta.get("column_type") or ""
        extra = []
        if pages is not None:
            extra.append(f"约 {pages} 页")
        if col:
            extra.append("单栏" if col == "single" else "双栏")
        registry_resolution = ((meta.get("registry_asset") or {}).get("resolution") or {})
        asset_status = registry_resolution.get("asset_status") or registry_resolution.get("venue_status")
        if asset_status:
            extra.append(f"资产:{asset_status}")
        suffix = f" — {', '.join(extra)}" if extra else ""
        out.append((key, f"{name} ({key}){suffix}"))
    out.append(("__custom__", "自定义模板 ID（templates.yaml 中已有键名）"))
    out.append(("__generic__", "通用 / 不选模板（仅指定栏型与页数）"))
    return out


def main() -> None:
    banner()
    templates = load_templates()
    venue_choices = build_venue_choices(templates)
    venue_key = select_from_list(
        "所属会刊 / 模板（来自包内 config/templates.yaml）",
        venue_choices,
    )

    template_id: Optional[str] = None
    column_type: Optional[str] = None
    default_pages: Optional[int] = None

    if venue_key == "__custom__":
        template_id = ask_text("请输入模板键名（与 templates.yaml 中一致）", "").strip() or None
        if template_id and template_id in templates:
            meta = templates[template_id]
            column_type = meta.get("column_type")
            default_pages = template_main_body_pages(meta)
        else:
            column_type = select_from_list(
                "栏型",
                [
                    ("single", "单栏"),
                    ("double", "双栏"),
                ],
            )
    elif venue_key == "__generic__":
        template_id = None
        column_type = select_from_list(
            "栏型",
            [
                ("single", "单栏"),
                ("double", "双栏"),
            ],
        )
    else:
        template_id = venue_key
        meta = templates.get(template_id) or {}
        column_type = meta.get("column_type")
        default_pages = template_main_body_pages(meta)

    main_tex = ask_text("主 TeX 文件路径", "main.tex")
    mt_path = Path(main_tex)
    if not mt_path.is_file():
        print(f"⚠️  未找到文件: {mt_path.resolve()}（仍将写入配置，请稍后修正路径）")

    if default_pages is None:
        default_pages = 9
    target_pages = ask_int("目标页数（整数）", int(default_pages))

    pb_labels = [f"{k} — {label}" for k, label in PAGE_BUDGET_CHOICES]
    pb_keys = [k for k, _ in PAGE_BUDGET_CHOICES]
    if select_questionary():
        from questionary import Choice  # type: ignore

        qn = select_questionary()
        qc = [Choice(title=l, value=k) for k, l in PAGE_BUDGET_CHOICES]
        r = qn.select(
            "页数口径（请与导师/会方要求一致）",
            choices=qc,
            default=pb_keys[1],
        ).ask()
        if r is None:
            sys.exit(0)
        page_budget = str(r)
    else:
        idx = _numbered_select(
            pb_labels,
            "页数口径（请与导师/会方要求一致）",
        )
        page_budget = pb_keys[idx]

    strict = ask_confirm("是否启用严格模式（strict_mode）？", default=False)
    max_rounds = ask_int("最大迭代轮数 max_rounds", 10)

    profile: Dict[str, Any] = {
        "version": "1.0",
        "main_tex": main_tex,
        "template": template_id,
        "column_type": column_type,
        "target_pages": target_pages,
        "page_budget_scope": page_budget,
        "strict_mode": strict,
        "max_rounds": max_rounds,
    }

    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    profile_path = data_dir / "paperfit-project.yaml"
    try:
        import yaml  # type: ignore

        with open(profile_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                profile,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
    except Exception as e:
        print(f"写入 {profile_path} 失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ 已写入 {profile_path.resolve()}")

    sm = package_root() / "scripts" / "state_manager.py"
    cmd = [
        sys.executable,
        str(sm),
        "init",
        main_tex,
        "--task",
        "full_vto",
        "--target-pages",
        str(target_pages),
        "--max-rounds",
        str(max_rounds),
        "--page-budget",
        page_budget,
    ]
    if template_id:
        cmd.extend(["--template", template_id])
    if column_type:
        cmd.extend(["--column-type", column_type])
    if strict:
        cmd.append("--strict")

    print("\n正在初始化 state.json …")
    r = subprocess.run(cmd, cwd=os.getcwd())
    if r.returncode != 0:
        sys.exit(r.returncode)

    print("\n下一步：在 Claude Code 中执行 /fix-layout 或 /show-status")


if __name__ == "__main__":
    main()
