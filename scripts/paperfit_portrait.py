#!/usr/bin/env python3
"""
生成并刷新「论文画像」data/paperfit-portrait.yaml：
- build：结合用户参数 + 扫描主 tex / 同主文件名 PDF（pdfinfo）+ 可选读 state 机检摘要
- refresh：在已有画像基础上重新扫描（每轮 VTO 后由调度器或用户触发）

供 Claude 斜杠命令 /paperfit 非交互调用；终端向导见 configure_wizard.py。
"""
from __future__ import annotations

# =============================================================================
# 终端安全保护 - 在导入其他库之前先设置（防止字体崩溃）
# =============================================================================

import sys
import re


class SafeOutput:
    """安全的输出包装器，过滤所有可能干扰终端的字符"""

    # ANSI 转义序列模式
    ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    # 其他控制字符（保留基本的换行和制表符）
    CONTROL_CHARS = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]')

    def __init__(self, stream):
        self.stream = stream
        self.buffer = ""

    def write(self, text):
        # 清理文本中的有害字符
        cleaned = self.ANSI_ESCAPE.sub('', text)
        cleaned = self.CONTROL_CHARS.sub('', cleaned)
        self.stream.write(cleaned)
        self.stream.flush()

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False  # 伪装成非终端，防止库输出颜色代码


# 保存原始 stderr
_original_stderr = sys.stderr

# 用安全包装器替换 stderr
sys.stderr = SafeOutput(_original_stderr)

# 同时捕获 stdout 以防万一
_original_stdout = sys.stdout
sys.stdout = SafeOutput(_original_stdout)

# =============================================================================
# 现在可以安全地导入其他库
# =============================================================================

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from template_registry import load_templates as load_templates_with_registry

STATE_PATH = Path("data/state.json")
PORTRAIT_PATH = Path("data/paperfit-portrait.yaml")
PROJECT_PATH = Path("data/paperfit-project.yaml")


def package_root() -> Path:
    env = os.environ.get("PAPERFIT_PACKAGE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def _yaml_load(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        print("请安装 PyYAML: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _yaml_dump(path: Path, data: Dict[str, Any]) -> None:
    import yaml  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def load_templates() -> Dict[str, Any]:
    return load_templates_with_registry(package_root())


def read_tex(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_tex_comments(tex: str) -> str:
    out: List[str] = []
    for line in tex.splitlines():
        cut = None
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                cut = i
                break
            i += 1
        out.append(line[:cut] if cut is not None else line)
    return "\n".join(out)


def preamble(tex: str) -> str:
    m = re.search(r"\\begin\{document\}", tex, re.IGNORECASE)
    if m:
        return tex[: m.start()]
    return tex[:25000]


def infer_column_from_preamble(pre: str) -> str:
    s = pre.lower()
    if re.search(r"\\documentclass\[[^\]]*twocolumn", s):
        return "double"
    if "\\twocolumn" in s:
        return "double"
    if "\\onecolumn" in s:
        return "single"
    if "twocolumn" in s and "documentclass" in s:
        return "double"
    return "unknown"


def count_includegraphics(tex: str) -> int:
    clean = strip_tex_comments(tex)
    return len(
        re.findall(
            r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}",
            clean,
            flags=re.DOTALL,
        )
    )


def count_begin_env(tex: str, env: str, include_star: bool = False) -> int:
    clean = strip_tex_comments(tex)
    suffix = r"\*?" if include_star else ""
    return len(re.findall(r"\\begin\{" + re.escape(env) + suffix + r"\}", clean, re.IGNORECASE))


def tex_stats(tex: str) -> Dict[str, Any]:
    clean = strip_tex_comments(tex)
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", clean))
    latin_words = len(re.findall(r"[A-Za-z][A-Za-z\-']{2,}", clean))
    lines = len(tex.splitlines())
    return {
        "cjk_char_count": cjk,
        "latin_word_like_count": latin_words,
        "main_tex_lines": lines,
    }


def find_pdf_for_main(main_tex: Path) -> Optional[Path]:
    stem = main_tex.stem
    parent = main_tex.parent
    cand = parent / f"{stem}.pdf"
    if cand.is_file():
        return cand
    for sub in ("build", "out", "output"):
        p = parent / sub / f"{stem}.pdf"
        if p.is_file():
            return p
    return None


def pdf_page_count(pdf: Path) -> Optional[int]:
    try:
        r = subprocess.run(
            ["pdfinfo", str(pdf)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            return None
        for line in (r.stdout or "").splitlines():
            if line.strip().lower().startswith("pages:"):
                return int(line.split(":", 1)[1].strip())
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired, OSError):
        return None
    return None


def load_state_slice() -> Dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    cv = st.get("cv_signals_summary") or {}
    return {
        "current_round": st.get("current_round"),
        "max_rounds": st.get("max_rounds"),
        "status": st.get("status"),
        "main_tex": st.get("main_tex"),
        "cv_a5_candidate_count": cv.get("a5_candidate_count"),
        "pages_flagged_count": cv.get("pages_flagged_count"),
        "compile_success": st.get("compile_success"),
        "page_images_rendered": st.get("page_images_rendered"),
    }


def effective_column(
    inferred: str,
    template_key: Optional[str],
    templates: Dict[str, Any],
    override: Optional[str],
) -> str:
    if override in ("single", "double"):
        return override
    if template_key and template_key in templates:
        ct = (templates[template_key] or {}).get("column_type")
        if ct in ("single", "double"):
            return ct
    if inferred in ("single", "double"):
        return inferred
    return "unknown"


def merge_project_yaml(user: Dict[str, Any]) -> None:
    existing = _yaml_load(PROJECT_PATH)
    merged = {**existing, **{k: v for k, v in user.items() if v is not None}}
    merged.setdefault("version", "1.0")
    _yaml_dump(PROJECT_PATH, merged)


def sync_state_portrait(portrait: Dict[str, Any], init_if_missing: bool) -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from state_manager import StateManager  # noqa: E402

    mgr = StateManager()
    scanned = portrait.get("scanned") or {}
    user = portrait.get("user") or {}
    patch: Dict[str, Any] = {
        "task": {
            "portrait_path": str(PORTRAIT_PATH.as_posix()),
            "portrait_refreshed_at": portrait.get("updated_at"),
            "portrait_scanned": {
                "column_type_effective": scanned.get("column_type_effective"),
                "includegraphics_count": scanned.get("includegraphics_count"),
                "figure_float_count": scanned.get("figure_float_count"),
                "table_float_count": scanned.get("table_float_count"),
                "figure_float_count_effective": scanned.get("figure_float_count_effective"),
                "table_float_count_effective": scanned.get("table_float_count_effective"),
                "figure_float_count_source": scanned.get("figure_float_count_source"),
                "table_float_count_source": scanned.get("table_float_count_source"),
                "user_observed_figure_float_count": scanned.get("user_observed_figure_float_count"),
                "user_observed_table_float_count": scanned.get("user_observed_table_float_count"),
                "count_discrepancies": scanned.get("count_discrepancies"),
                "count_note": scanned.get("count_note"),
                "pdf_page_count": scanned.get("pdf_page_count"),
                "cjk_char_count": scanned.get("cjk_char_count"),
                "latin_word_like_count": scanned.get("latin_word_like_count"),
            },
        }
    }

    if STATE_PATH.is_file():
        mgr.load()
        mgr.update(patch)
        if user.get("main_tex"):
            mgr.update({"main_tex": user["main_tex"]})
        tpatch: Dict[str, Any] = {}
        if user.get("template") is not None:
            tpatch["template"] = user.get("template")
        if user.get("page_budget_scope"):
            tpatch["page_budget_scope"] = user["page_budget_scope"]
        if user.get("target_pages") is not None:
            tpatch["target_pages"] = user["target_pages"]
        if user.get("strict_mode") is not None:
            tpatch["strict_mode"] = user["strict_mode"]
        ce = scanned.get("column_type_effective")
        if ce in ("single", "double"):
            tpatch["column_type"] = ce
        if tpatch:
            mgr.update({"task": tpatch})
    elif init_if_missing:
        main_tex = user.get("main_tex") or "main.tex"
        mgr.init_state(
            main_tex,
            task_type="full_vto",
            target_pages=user.get("target_pages"),
            template=user.get("template"),
            strict_mode=bool(user.get("strict_mode")),
            max_rounds=int(user.get("max_rounds") or 10),
            column_type=scanned.get("column_type_effective")
            if scanned.get("column_type_effective") in ("single", "double")
            else None,
            page_budget_scope=user.get("page_budget_scope"),
        )
        mgr.update(patch)
    else:
        print(
            "提示: 无 data/state.json 且未使用 build — 仅写入画像文件，未改 state。",
            file=sys.stderr,
        )


def run_scan(
    main_tex: Path,
    templates: Dict[str, Any],
    template_key: Optional[str],
    column_override: Optional[str],
) -> Dict[str, Any]:
    if not main_tex.is_file():
        raise FileNotFoundError(f"主 TeX 不存在: {main_tex.resolve()}")
    raw = read_tex(main_tex)
    pre = preamble(raw)
    inferred = infer_column_from_preamble(pre)
    docclass_m = re.search(r"\\documentclass[^\n]{0,200}", pre)
    docclass_snippet = docclass_m.group(0)[:200] if docclass_m else None
    eff = effective_column(inferred, template_key, templates, column_override)
    stats = tex_stats(raw)
    pdf = find_pdf_for_main(main_tex)
    pdf_pages = pdf_page_count(pdf) if pdf else None
    return {
        "column_type_inferred": inferred,
        "column_type_effective": eff,
        "documentclass_line_snippet": docclass_snippet,
        "includegraphics_count": count_includegraphics(raw),
        "figure_float_count": count_begin_env(raw, "figure", include_star=True),
        "table_float_count": count_begin_env(raw, "table", include_star=True),
        "pdf_rel_path": os.path.relpath(pdf, Path.cwd()) if pdf else None,
        "pdf_page_count": pdf_pages,
        **stats,
    }


def apply_user_count_observations(
    scanned: Dict[str, Any],
    observed_table_count: Optional[int] = None,
    observed_figure_count: Optional[int] = None,
    count_note: Optional[str] = None,
) -> Dict[str, Any]:
    merged = dict(scanned)
    discrepancies: Dict[str, Dict[str, int]] = {}

    table_scanned = merged.get("table_float_count")
    if observed_table_count is not None:
        merged["user_observed_table_float_count"] = observed_table_count
        merged["table_float_count_effective"] = observed_table_count
        merged["table_float_count_source"] = "user_observed"
        if table_scanned is not None and table_scanned != observed_table_count:
            discrepancies["table_float_count"] = {
                "scanned": int(table_scanned),
                "user_observed": int(observed_table_count),
                "delta": int(observed_table_count) - int(table_scanned),
            }
    else:
        merged["table_float_count_effective"] = table_scanned
        merged["table_float_count_source"] = "scanner"

    figure_scanned = merged.get("figure_float_count")
    if observed_figure_count is not None:
        merged["user_observed_figure_float_count"] = observed_figure_count
        merged["figure_float_count_effective"] = observed_figure_count
        merged["figure_float_count_source"] = "user_observed"
        if figure_scanned is not None and figure_scanned != observed_figure_count:
            discrepancies["figure_float_count"] = {
                "scanned": int(figure_scanned),
                "user_observed": int(observed_figure_count),
                "delta": int(observed_figure_count) - int(figure_scanned),
            }
    else:
        merged["figure_float_count_effective"] = figure_scanned
        merged["figure_float_count_source"] = "scanner"

    if count_note:
        merged["count_note"] = count_note
    if discrepancies:
        merged["count_discrepancies"] = discrepancies

    return merged


def cmd_build(args: argparse.Namespace) -> None:
    templates = load_templates()
    main_path = Path(args.main).resolve()
    try:
        main_rel = str(main_path.relative_to(Path.cwd()))
    except ValueError:
        main_rel = str(args.main)

    tmeta = templates.get(args.template) or {} if args.template else {}
    venue_display = tmeta.get("name") if args.template else None

    user: Dict[str, Any] = {
        "main_tex": main_rel,
        "template": args.template,
        "venue_display": venue_display,
        "page_budget_scope": args.page_budget,
        "target_pages": args.target_pages,
        "strict_mode": args.strict,
        "max_rounds": args.max_rounds,
        "observed_table_float_count": args.observed_table_count,
        "observed_figure_float_count": args.observed_figure_count,
        "count_note": args.count_note,
    }

    scanned = run_scan(main_path, templates, args.template, args.column_type)
    scanned = apply_user_count_observations(
        scanned,
        observed_table_count=args.observed_table_count,
        observed_figure_count=args.observed_figure_count,
        count_note=args.count_note,
    )
    from_state = load_state_slice()

    portrait: Dict[str, Any] = {
        "version": "1.1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "build",
        "user": {k: v for k, v in user.items() if v is not None},
        "scanned": scanned,
        "from_state": from_state,
    }
    _yaml_dump(PORTRAIT_PATH, portrait)
    merge_project_yaml(user)
    sync_state_portrait(portrait, init_if_missing=True)
    print(json.dumps({"ok": True, "portrait": str(PORTRAIT_PATH.resolve())}, ensure_ascii=False))


def cmd_refresh(args: argparse.Namespace) -> None:
    templates = load_templates()
    prev = _yaml_load(PORTRAIT_PATH)
    user = dict(prev.get("user") or {})
    if args.main:
        main_path = Path(args.main).resolve()
        try:
            user["main_tex"] = str(main_path.relative_to(Path.cwd()))
        except ValueError:
            user["main_tex"] = str(args.main)
    elif user.get("main_tex"):
        main_path = (Path.cwd() / user["main_tex"]).resolve()
    elif STATE_PATH.is_file():
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        mt = st.get("main_tex") or "main.tex"
        user["main_tex"] = mt
        main_path = (Path.cwd() / mt).resolve()
    else:
        print("refresh 需要已有 data/paperfit-portrait.yaml、--main 或 data/state.json", file=sys.stderr)
        sys.exit(1)

    template_key = user.get("template")
    if args.observed_table_count is not None:
        user["observed_table_float_count"] = args.observed_table_count
    if args.observed_figure_count is not None:
        user["observed_figure_float_count"] = args.observed_figure_count
    if args.count_note is not None:
        user["count_note"] = args.count_note

    scanned = run_scan(main_path, templates, template_key, args.column_type)
    scanned = apply_user_count_observations(
        scanned,
        observed_table_count=user.get("observed_table_float_count"),
        observed_figure_count=user.get("observed_figure_float_count"),
        count_note=user.get("count_note"),
    )
    from_state = load_state_slice()

    portrait: Dict[str, Any] = {
        "version": "1.1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "refresh",
        "user": user,
        "scanned": scanned,
        "from_state": from_state,
    }
    _yaml_dump(PORTRAIT_PATH, portrait)
    merge_project_yaml(user)
    sync_state_portrait(portrait, init_if_missing=False)
    print(json.dumps({"ok": True, "portrait": str(PORTRAIT_PATH.resolve())}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperFit 论文画像 build / refresh")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="冷启动：用户参数 + 扫描 tex/pdf，写画像并 init/update state")
    b.add_argument("--main", required=True, help="主 .tex 路径")
    b.add_argument(
        "--template",
        default=None,
        help="templates.yaml 中的模板键；省略则仅靠扫描推断栏型",
    )
    b.add_argument(
        "--page-budget",
        required=True,
        choices=["main_body", "with_refs", "with_appendix"],
        help="页数口径",
    )
    b.add_argument("--target-pages", type=int, required=True, help="目标页数")
    b.add_argument("--strict", action="store_true", help="strict_mode")
    b.add_argument("--max-rounds", type=int, default=10)
    b.add_argument("--column-type", choices=["single", "double"], default=None)
    b.add_argument("--observed-table-count", type=int, default=None, help="用户确认的实际表格数量")
    b.add_argument("--observed-figure-count", type=int, default=None, help="用户确认的实际图片数量")
    b.add_argument("--count-note", default=None, help="用户对数量偏差的补充说明")

    r = sub.add_parser("refresh", help="每轮刷新：重扫 tex/pdf，合并 state 摘要，更新画像与 state 钩子")
    r.add_argument("--main", default=None, help="覆盖主 tex（默认来自画像或 state）")
    r.add_argument("--column-type", choices=["single", "double"], default=None)
    r.add_argument("--observed-table-count", type=int, default=None, help="用户确认的实际表格数量")
    r.add_argument("--observed-figure-count", type=int, default=None, help="用户确认的实际图片数量")
    r.add_argument("--count-note", default=None, help="用户对数量偏差的补充说明")

    args = parser.parse_args()
    if args.cmd == "build":
        cmd_build(args)
    elif args.cmd == "refresh":
        cmd_refresh(args)


if __name__ == "__main__":
    main()
