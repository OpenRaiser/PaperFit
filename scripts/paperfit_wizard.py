#!/usr/bin/env python3
"""
PaperFit 终端配置向导 - 固定问卷形式
用于 /paperfit 命令，提供快速交互配置体验
"""
from __future__ import annotations

import sys
import json
from datetime import datetime
from pathlib import Path

from template_registry import load_templates as load_templates_with_registry

# 终端安全保护（防止 ANSI 转义序列污染）
import re as _re
class SafeOutput:
    ANSI_ESCAPE = _re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    CONTROL_CHARS = _re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]')
    def __init__(self, stream):
        self.stream = stream
    def write(self, text):
        cleaned = self.ANSI_ESCAPE.sub('', text)
        cleaned = self.CONTROL_CHARS.sub('', cleaned)
        self.stream.write(cleaned)
        self.stream.flush()
    def flush(self):
        self.stream.flush()
    def isatty(self):
        return False

sys.stderr = SafeOutput(sys.stderr)
sys.stdout = SafeOutput(sys.stdout)

try:
    import questionary
    from questionary import Style
except ImportError:
    print("错误：需要安装 questionary: pip install questionary", file=sys.stderr)
    sys.exit(1)

# 自定义样式
custom_style = Style([
    ("qmark", "fg:#6C50D0 bold"),
    ("question", "bold"),
    ("answer", "fg:#FF9D00 bold"),
    ("pointer", "fg:#6C50D0 bold"),
    ("selected", "fg:#CC5454"),
    ("highlighted", "fg:#6C50D0 bold"),
    ("instruction", "fg:#888"),
])


STATE_PATH = Path("data/state.json")
PORTRAIT_PATH = Path("data/paperfit-portrait.yaml")


def load_templates() -> list:
    """加载模板列表"""
    templates = load_templates_with_registry(Path(__file__).parent.parent)
    if not templates:
        return [
            {"name": "ICLR 2025", "value": "ICLR2025", "pages": 9},
            {"name": "ICML 2025", "value": "ICML2025", "pages": 9},
            {"name": "NeurIPS 2024", "value": "NeurIPS2024", "pages": 9},
            {"name": "CVPR 2025", "value": "CVPR2025", "pages": 8},
            {"name": "ECCV 2024", "value": "ECCV2024", "pages": 14},
            {"name": "ICCV 2025", "value": "ICCV2025", "pages": 8},
            {"name": "AAAI 2025", "value": "AAAI2025", "pages": 7},
            {"name": "ACL 2025", "value": "ACL2025", "pages": 8},
            {"name": "EMNLP 2024", "value": "EMNLP2024", "pages": 8},
        ]

    result = []
    for key, value in templates.items():
        expected_pages = value.get("expected_pages") if isinstance(value, dict) else None
        pages = 9
        if isinstance(expected_pages, dict) and isinstance(expected_pages.get("main_body"), int):
            pages = int(expected_pages["main_body"])
        result.append({
            "name": f"{key} ({pages}页)",
            "value": key,
            "pages": pages,
        })
    return result


def infer_column_type(main_tex_path: Path) -> str:
    """从 tex 文件推断栏型"""
    if not main_tex_path.exists():
        return "double"  # 默认双栏

    try:
        head = main_tex_path.read_text(encoding='utf-8', errors='replace')[:2000]
        if "twocolumn" in head.lower():
            return "double"
        if "onecolumn" in head.lower():
            return "single"
        # 常见双栏模板
        double_templates = ["cvpr", "iccv", "eccv", "iclr", "icml", "neurips", "aaai"]
        for t in double_templates:
            if t in head.lower():
                return "double"
        return "single"
    except:
        return "double"


def find_main_tex() -> str:
    """查找可能的 tex 文件"""
    candidates = ["main.tex", "paper.tex", "aaai24_antibody.tex", "ms.tex"]
    for c in candidates:
        if Path(c).exists():
            return c

    # 查找当前目录第一个 tex 文件
    tex_files = list(Path(".").glob("*.tex"))
    if tex_files:
        return str(tex_files[0])

    return "main.tex"


def run_wizard():
    """运行配置向导"""
    print("\n╔════════════════════════════════════════════════════╗")
    print("║       PaperFit 配置向导 - 视觉排版优化系统          ║")
    print("╚════════════════════════════════════════════════════╝\n")

    # 1. 选择投稿目标
    templates = load_templates()
    template_answer = questionary.select(
        "1. 选择投稿目标（空格选择，回车确认）",
        choices=templates,
        style=custom_style,
        instruction="按上下键选择，空格选中，回车确认",
    ).ask()

    if template_answer is None:
        print("\n已取消配置。")
        return None

    selected_template = template_answer
    selected_template_key = None
    suggested_pages = 9
    for t in templates:
        if t["name"] == template_answer or t["value"] == template_answer:
            selected_template_key = t["value"]
            suggested_pages = t.get("pages", 9)
            break

    # 2. 目标页数
    target_pages = questionary.text(
        f"2. 目标页数（建议 {suggested_pages} 页）:",
        default=str(suggested_pages),
        style=custom_style,
        validate=lambda x: x.isdigit() or "请输入数字",
    ).ask()

    if target_pages is None:
        print("\n已取消配置。")
        return None

    target_pages = int(target_pages)

    # 3. 栏型选择
    main_tex_path = Path(find_main_tex())
    inferred_column = infer_column_type(main_tex_path)
    column_default = "双栏 (twocolumn)" if inferred_column == "double" else "单栏 (onecolumn)"

    column_answer = questionary.select(
        "3. 栏型选择:",
        choices=[
            "双栏 (twocolumn)",
            "单栏 (onecolumn)",
        ],
        default=column_default,
        style=custom_style,
    ).ask()

    if column_answer is None:
        print("\n已取消配置。")
        return None

    column_type = "double" if "双栏" in column_answer else "single"

    # 4. 主 tex 文件路径
    main_tex_answer = questionary.text(
        "4. 主 .tex 文件路径:",
        default=str(main_tex_path),
        style=custom_style,
    ).ask()

    if main_tex_answer is None:
        print("\n已取消配置。")
        return None

    main_tex_path = Path(main_tex_answer)

    # 5. 页数口径
    page_budget = questionary.select(
        "5. 页数口径:",
        choices=[
            ("仅正文（不含参考文献）", "main_body"),
            ("正文 + 参考文献", "with_refs"),
            ("正文 + 参考文献 + 附录", "with_appendix"),
        ],
        default="with_refs",
        style=custom_style,
    ).ask()

    if page_budget is None:
        print("\n已取消配置。")
        return None

    # 汇总确认
    print("\n" + "═" * 50)
    print("配置汇总确认")
    print("═" * 50)
    print(f"  投稿目标：  {selected_template_key}")
    print(f"  目标页数：  {target_pages} 页")
    print(f"  栏型：      {'双栏' if column_type == 'double' else '单栏'}")
    print(f"  主文件：    {main_tex_path}")
    print(f"  页数口径：  {page_budget}")
    print("═" * 50)

    confirm = questionary.confirm(
        "确认配置并生成画像？",
        default=True,
        style=custom_style,
    ).ask()

    if not confirm:
        print("\n已取消配置。")
        return None

    # 生成配置
    return {
        "template": selected_template_key,
        "target_pages": target_pages,
        "column_type": column_type,
        "main_tex": str(main_tex_path),
        "page_budget": page_budget,
        "inferred_at": datetime.now().isoformat(),
    }


def save_portrait(answers: dict):
    """保存画像到 YAML"""
    import yaml

    PORTRAIT_PATH.parent.mkdir(parents=True, exist_ok=True)

    portrait_data = {
        "user_constraints": {
            "template": answers["template"],
            "target_pages": answers["target_pages"],
            "page_budget": answers["page_budget"],
        },
        "scanned": {
            "main_tex": answers["main_tex"],
            "column_type": answers["column_type"],
            "inferred_at": answers["inferred_at"],
            "confidence": "quick",  # 快速推断，后续会刷新
        },
        "from_state": {},
    }

    with open(PORTRAIT_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(portrait_data, f, allow_unicode=True, default_flow_style=False)

    # 同步更新 state.json
    if STATE_PATH.exists():
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            state = json.load(f)
    else:
        state = {}

    state["task"] = state.get("task", {})
    state["task"]["portrait_path"] = str(PORTRAIT_PATH)
    state["task"]["portrait_refreshed_at"] = datetime.now().isoformat()

    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    return portrait_data


def show_next_steps():
    """显示下一步指引"""
    print("\n" + "═" * 50)
    print("✓ 配置完成！下一步建议：")
    print("═" * 50)
    print("""
  /fix-layout      开始完整 VTO 优化（编译→页图→缺陷修复闭环）
  /check-visual    仅视觉检测，不自动修改
  /show-status     查看当前状态和轮次进度
  /paperfit        重新配置或刷新画像
    """)
    print("═" * 50)


def main():
    """主入口"""
    # 检查必要依赖
    try:
        import yaml
    except ImportError:
        print("错误：需要安装 PyYAML: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    # 运行向导
    answers = run_wizard()

    if answers is None:
        sys.exit(0)

    # 保存配置
    save_portrait(answers)

    # 显示下一步
    show_next_steps()

    print("\n快速开始：直接输入 /fix-layout 启动 VTO 优化\n")


if __name__ == "__main__":
    main()
