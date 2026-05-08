#!/usr/bin/env python3
"""
极速模式论文画像推断（< 500ms）
用于 /paperfit 命令的快速响应，无需等待完整扫描
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 复用安全输出（如果主文件已导入）
try:
    from paperfit_portrait import SafeOutput, _original_stderr, _original_stdout
except ImportError:
    # 独立运行时的安全保护
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


@dataclass
class QuickPortrait:
    """极速推断的论文画像"""
    inferred_at: datetime
    main_tex: Path
    template: Optional[str] = None
    column_type: str = "single"
    target_pages: int = 9
    figures_count: int = 0
    tables_count: int = 0
    word_estimate: int = 0
    confidence: str = "low"  # low: 快速推断, high: 完整扫描后

    def to_dict(self) -> Dict:
        return {
            "inferred_at": self.inferred_at.isoformat(),
            "main_tex": str(self.main_tex),
            "template": self.template,
            "column_type": self.column_type,
            "target_pages": self.target_pages,
            "figures_count": self.figures_count,
            "tables_count": self.tables_count,
            "word_estimate": self.word_estimate,
            "confidence": self.confidence,
        }


def read_tex_head(main_tex: Path, lines: int = 100) -> str:
    """读取 tex 文件前 N 行"""
    try:
        with open(main_tex, 'r', encoding='utf-8', errors='replace') as f:
            return ''.join([f.readline() for _ in range(lines)])
    except Exception as e:
        print(f"警告: 无法读取 {main_tex}: {e}", file=sys.stderr)
        return ""


def infer_template_from_preamble(tex_head: str) -> Optional[str]:
    """从导言区推断模板类型"""
    # Match \documentclass[...]{...} in the preamble.
    match = re.search(r'\\documentclass\[([^\]]*)\]\{([^}]+)\}', tex_head)
    if not match:
        return None

    options, docclass = match.groups()
    options = options.lower()
    docclass = docclass.lower()

    # 常见会议/期刊映射
    mappings = {
        'aaai': 'AAAI',
        'icml': 'ICML',
        'iclr': 'ICLR',
        'neurips': 'NeurIPS',
        'cvpr': 'CVPR',
        'eccv': 'ECCV',
        'iccv': 'ICCV',
        'acl': 'ACL',
        'emnlp': 'EMNLP',
        'naacl': 'NAACL',
        'sigconf': 'ACM',
        'ieee': 'IEEE',
    }

    for key, value in mappings.items():
        if key in docclass or key in options:
            return value

    return docclass.upper()


def quick_count_figures(main_tex: Path) -> int:
    """快速统计 figure 数量（基于文件存在性）"""
    parent = main_tex.parent

    # 检查常见图目录
    fig_dirs = ['figs', 'figures', 'images', 'imgs']
    count = 0

    for fig_dir in fig_dirs:
        fig_path = parent / fig_dir
        if fig_path.exists():
            # 只统计一层，避免递归太慢
            count += len(list(fig_path.glob('*.png')) + list(fig_path.glob('*.pdf')) + list(fig_path.glob('*.jpg')))

    # 如果没有专用目录，尝试在 tex 中计数
    if count == 0:
        tex_head = read_tex_head(main_tex, 500)
        count = len(re.findall(r'\\begin\{figure', tex_head))

    return min(count, 20)  # 快速模式上限


def suggest_target_pages(template: Optional[str], figures: int) -> int:
    """基于模板和图表数建议目标页数"""
    defaults = {
        'ICLR': 9,
        'ICML': 9,
        'NeurIPS': 9,
        'CVPR': 8,
        'ECCV': 14,
        'ICCV': 8,
        'AAAI': 7,
        'ACL': 8,
        'EMNLP': 8,
    }

    base = defaults.get(template, 9) if template else 9

    # 图表多可适当增加
    if figures > 10:
        base += 2
    elif figures > 5:
        base += 1

    return min(base, 20)


def quick_infer(main_tex: Path) -> QuickPortrait:
    """
    极速推断论文画像（< 500ms）
    用于 /paperfit 命令的快速响应
    """
    start_time = datetime.now()

    # 1. 快速读取 tex 头部
    tex_head = read_tex_head(main_tex, 100)

    # 2. 推断模板和栏型
    template = infer_template_from_preamble(tex_head)
    column_type = "double" if "twocolumn" in tex_head.lower() else "single"

    # 3. 快速计数（非递归）
    figures_count = quick_count_figures(main_tex)

    # 4. 建议目标页数
    target_pages = suggest_target_pages(template, figures_count)

    # 5. 估算字数（基于 tex 文件大小）
    try:
        file_size = main_tex.stat().st_size
        word_estimate = file_size // 6  # 粗略估计：平均 6 字节/词
    except:
        word_estimate = 0

    elapsed = (datetime.now() - start_time).total_seconds()

    return QuickPortrait(
        inferred_at=datetime.now(),
        main_tex=main_tex,
        template=template,
        column_type=column_type,
        target_pages=target_pages,
        figures_count=figures_count,
        tables_count=0,  # 快速模式不统计表
        word_estimate=word_estimate,
        confidence="low" if elapsed < 1.0 else "medium",
    )


def main():
    """CLI 入口（用于测试）"""
    import json

    main_tex = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("main.tex")

    if not main_tex.exists():
        print(f"错误: 找不到 {main_tex}", file=sys.stderr)
        sys.exit(1)

    portrait = quick_infer(main_tex)
    print(json.dumps(portrait.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
