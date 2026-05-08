#!/usr/bin/env python3
"""
缺陷注入脚本 - 用于构建 VTO Benchmark 测试集

向干净的 LaTeX 论文中注入已知的、可检测的排版缺陷，用于：
1. 测试缺陷检测算法的准确性
2. 评估修复策略的有效性
3. 建立回归测试基准

支持的缺陷类型（按 VTO 分类体系）：
- Category A: 空间利用缺陷（孤行寡行、末页留白、页数预算、双栏不齐）
- Category B: 浮动体缺陷（远离引用、尺寸不适配、连续堆叠、跨页分裂）
- Category C: 一致性缺陷（表格字号不一、图片风格不一致）
- Category D: 溢出缺陷（overfull hbox、长公式溢出、URL 溢出）
- Category E: 跨模板缺陷（单双栏失配）
"""

import argparse
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# 缺陷注入配置
# ============================================================

@dataclass
class DefectConfig:
    """缺陷配置"""
    defect_id: str
    category: str
    name: str
    description: str
    severity: str  # "minor" | "major" | "critical"
    injection_method: str  # 注入方法描述


DEFECT_CATALOG = [
    # Category A: 空间利用缺陷
    DefectConfig(
        defect_id="A1-widow-orphan",
        category="A",
        name="孤行寡行",
        description="在段落末尾添加短行模拟孤行",
        severity="major",
        injection_method="add short last line to paragraph"
    ),
    DefectConfig(
        defect_id="A2-trailing-whitespace",
        category="A",
        name="末页留白",
        description="在文档末尾添加\\vspace 制造大面积留白",
        severity="minor",
        injection_method="add \\\\vspace before end"
    ),

    # Category B: 浮动体缺陷
    DefectConfig(
        defect_id="B1-float-placement",
        category="B",
        name="浮动体远离引用",
        description="将浮动体位置参数改为 [p] 强制独立页面",
        severity="major",
        injection_method="change float placement to [p]"
    ),
    DefectConfig(
        defect_id="B2-float-width",
        category="B",
        name="浮动体尺寸不适配",
        description="将图片宽度设为 1.5\\linewidth 超出栏宽",
        severity="major",
        injection_method="increase figure width to 1.5\\\\linewidth"
    ),
    DefectConfig(
        defect_id="B3-float-clustering",
        category="B",
        name="浮动体堆叠",
        description="连续插入多个浮动体无正文间隔",
        severity="major",
        injection_method="add consecutive floats without text"
    ),

    # Category D: 溢出缺陷
    DefectConfig(
        defect_id="D1-overfull-hbox",
        category="D",
        name="Overfull hbox",
        description="添加超长无断点单词导致溢出",
        severity="major",
        injection_method="add very long word without hyphenation"
    ),
    DefectConfig(
        defect_id="D2-long-formula",
        category="D",
        name="长公式溢出",
        description="添加超宽公式环境",
        severity="major",
        injection_method="add wide formula"
    ),
    DefectConfig(
        defect_id="D3-url-overflow",
        category="D",
        name="URL 溢出",
        description="添加裸 URL（不用\\url{}包裹）",
        severity="minor",
        injection_method="add bare URL without \\\\url wrapper"
    ),
]


# ============================================================
# 缺陷注入器
# ============================================================

class DefectInjector:
    """缺陷注入器"""

    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)
        self.injected_defects: List[Dict] = []

    def inject_all(
        self,
        tex_content: str,
        defect_types: Optional[List[str]] = None,
    ) -> Tuple[str, List[Dict]]:
        """
        向 TeX 内容注入指定类型的缺陷

        Args:
            tex_content: 原始 TeX 内容
            defect_types: 要注入的缺陷类型列表（如 ["A1", "B2"]），None 表示全部

        Returns:
            (modified_content, injected_defects_list)
        """
        self.injected_defects = []
        modified_content = tex_content

        # 选择要注入的缺陷类型
        if defect_types is None:
            selected_defects = DEFECT_CATALOG
        else:
            selected_defects = [
                d for d in DEFECT_CATALOG
                if any(d.defect_id.startswith(t) for t in defect_types)
            ]

        # 按类别分组注入，累积修改
        for defect in selected_defects:
            modified_content = self._inject_defect(modified_content, defect)

        return modified_content, self.injected_defects

    def _inject_defect(self, tex_content: str, config: DefectConfig) -> str:
        """注入单个缺陷，返回修改后的内容"""
        # defect_id 如 "A1-widow-orphan" -> "a1_widow_orphan" (小写)
        method_name = f"_inject_{config.defect_id.replace('-', '_').lower()}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return method(tex_content, config)
        return tex_content

    def _inject_a1_widow_orphan(self, tex_content: str, config: DefectConfig) -> str:
        """注入孤行寡行缺陷 - 在段落末尾添加短行"""
        # 查找\lipsum 调用，在其后添加短行
        lipsum_pattern = r'(\\lipsum\[\d+\])'

        def add_widow(match):
            return match.group(1) + "\n\n% INJECTED: Widow/Orphan test\nShort line."

        modified, count = re.subn(lipsum_pattern, add_widow, tex_content, count=2)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified

    def _inject_a2_trailing_whitespace(self, tex_content: str, config: DefectConfig) -> str:
        """注入末页留白缺陷"""
        # 在\end{document}前添加大间距
        end_pattern = r'(\\end\{document\})'
        replacement = r'\\vspace{5cm}\n% INJECTED: Trailing whitespace\n\1'

        modified = re.sub(end_pattern, replacement, tex_content, count=1)

        if modified != tex_content:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": 1,
            })

        return modified

    def _inject_b1_float_placement(self, tex_content: str, config: DefectConfig) -> str:
        """注入浮动体远离引用缺陷"""
        # 将 [htbp] 改为 [p]
        float_pattern = r'\\begin\{(figure|table)\}\[htbp\]'

        def worsen_placement(match):
            env_type = match.group(1)
            return f"\\begin{{{env_type}}}[p]"

        modified, count = re.subn(float_pattern, worsen_placement, tex_content)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified

    def _inject_b2_float_width(self, tex_content: str, config: DefectConfig) -> str:
        """注入浮动体尺寸不适配缺陷"""
        # 将 0.8\\linewidth 改为 1.5\\linewidth
        width_pattern = r'width\s*=\s*0\.8\\linewidth'
        replacement = r'width=1.5\\linewidth'

        modified, count = re.subn(width_pattern, replacement, tex_content)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified

    def _inject_b3_float_clustering(self, tex_content: str, config: DefectConfig) -> str:
        """注入浮动体堆叠缺陷"""
        # 在现有 figure 后添加额外的 figure
        end_figure_pattern = r'(\\end\{figure\})'
        # 注意：替换字符串中 \\\\ 会被解释为单个 \，\1 是反向引用
        extra_figure = (
            r'\1'
            '\n% INJECTED: Float clustering - extra figure without text separation'
            '\n\\\\begin{figure}[h]'
            '\n\\\\centering'
            '\n\\\\includegraphics[width=0.5\\\\linewidth]{example-image-a}'
            '\n\\\\caption{Injected figure causing clustering.}'
            '\n\\\\label{fig:injected}'
            '\n\\\\end{figure}'
        )

        modified, count = re.subn(end_figure_pattern, extra_figure, tex_content, count=1)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified

    def _inject_d1_overfull_hbox(self, tex_content: str, config: DefectConfig) -> str:
        """注入 overfull hbox 缺陷"""
        # 添加超长单词
        long_word = "Supercalifragilisticexpialidocious" * 3  # 非常长的单词

        # 在段落中插入
        lipsum_pattern = r'(\\lipsum\[\d+\])'
        replacement = f'\\1\n\n% INJECTED: Overfull hbox test\n{long_word}'

        modified, count = re.subn(lipsum_pattern, replacement, tex_content, count=1)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified

    def _inject_d2_long_formula(self, tex_content: str, config: DefectConfig) -> str:
        """注入长公式溢出缺陷"""
        # 添加超宽公式 - 使用双反斜杠避免转义问题
        long_formula = r'''
% INJECTED: Long formula overflow
\\begin{equation}
f(x) = a_0 + a_1 x + a_2 x^2 + a_3 x^3 + a_4 x^4 + a_5 x^5 + a_6 x^6 + a_7 x^7 + a_8 x^8 + a_9 x^9 + a_{10} x^{10}
\\end{equation}
'''
        # 在现有 equation 后添加
        end_equation_pattern = r'(\\end\{equation\})'
        modified = re.sub(end_equation_pattern, r'\1' + long_formula, tex_content, count=1)

        if modified != tex_content:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": 1,
            })

        return modified

    def _inject_d3_url_overflow(self, tex_content: str, config: DefectConfig) -> str:
        """注入 URL 溢出缺陷"""
        # 添加裸 URL
        bare_url = "https://www.example.com/this/is/a/very/long/path/that/will/cause/overflow/in/the/document/and/create/a/line/that/exceeds/the/page/width/and/causes/an/error"

        # 在现有 URL 附近添加
        url_pattern = r'(\\url\{[^}]+\})'
        replacement = f'\\1\n\n% INJECTED: URL overflow test\n{bare_url}'

        modified, count = re.subn(url_pattern, replacement, tex_content, count=1)

        if count > 0:
            self.injected_defects.append({
                "defect_id": config.defect_id,
                "action": config.injection_method,
                "count": count,
            })

        return modified


# ============================================================
# 样本生成器
# ============================================================

class SampleGenerator:
    """生成包含特定缺陷的测试样本"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_clean_sample(self, name: str = "clean_sample") -> Path:
        """生成干净的测试样本（无缺陷）"""
        content = self._create_minimal_latex_sample()
        output_path = self.output_dir / f"{name}.tex"
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def generate_defective_sample(
        self,
        name: str,
        defect_types: List[str],
        seed: int = 42,
    ) -> Tuple[Path, List[Dict]]:
        """生成包含指定缺陷的样本"""
        # 先生成干净样本
        clean_path = self.generate_clean_sample(f"{name}_base")

        # 读取并注入缺陷
        content = clean_path.read_text(encoding="utf-8")
        injector = DefectInjector(seed=seed)
        modified_content, defects = injector.inject_all(content, defect_types)

        # 保存缺陷样本
        output_path = self.output_dir / f"{name}.tex"
        output_path.write_text(modified_content, encoding="utf-8")

        # 保存缺陷清单
        manifest_path = self.output_dir / f"{name}_defects.json"
        manifest_path.write_text(
            json.dumps(defects, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return output_path, defects

    def _create_minimal_latex_sample(self) -> str:
        """创建最小化 LaTeX 论文样本"""
        return r"""\documentclass[12pt,a4paper]{article}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{amsmath}
\usepackage{hyperref}
\usepackage{lipsum}

\title{Test Paper for VTO Benchmark}
\author{Test Author}
\date{\today}

\begin{document}

\maketitle

\begin{abstract}
This is a test document for VTO (Visual Typesetting Optimization) benchmark.
It contains various LaTeX elements that can be used to test defect detection.
\end{abstract}

\section{Introduction}
\label{sec:intro}
\lipsum[1]

\section{Methodology}
\label{sec:method}

\lipsum[2]

\begin{figure}[htbp]
\centering
\includegraphics[width=0.8\linewidth]{example-image}
\caption{A test figure.}
\label{fig:test}
\end{figure}

As shown in Figure~\ref{fig:test}, the methodology is straightforward.

\lipsum[3]

\begin{table}[htbp]
\centering
\begin{tabular}{lll}
\toprule
Method & Accuracy & Speed \\
\midrule
Baseline & 85.2\% & Fast \\
Ours & 92.1\% & Medium \\
\bottomrule
\end{tabular}
\caption{Comparison results.}
\label{tab:results}
\end{table}

Table~\ref{tab:results} shows the comparison results.

\section{Experiments}
\label{sec:experiments}

\lipsum[4-5]

The long equation below demonstrates formula handling:
\begin{equation}
f(x) = \sum_{n=0}^{\infty} \frac{f^{(n)}(0)}{n!} x^n
\end{equation}

\lipsum[6]

For more information, visit \url{https://www.example.com/very/long/path}

\section{Conclusion}
\label{sec:conclusion}
\lipsum[7]

\bibliographystyle{plain}
\begin{thebibliography}{9}
\bibitem{test} Test Reference. Example Paper Title. Journal, 2024.
\end{thebibliography}

\end{document}
"""


# ============================================================
# 主函数
# ============================================================

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="缺陷注入脚本 - 生成 VTO Benchmark 测试集"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/benchmarks/samples",
        help="输出目录"
    )
    parser.add_argument(
        "--defect-types",
        nargs="+",
        default=None,
        help="要注入的缺陷类型（如 A B1 D3），默认全部"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子"
    )
    parser.add_argument(
        "--list-defects",
        action="store_true",
        help="列出所有支持的缺陷类型"
    )

    args = parser.parse_args()

    # 列出缺陷类型
    if args.list_defects:
        print("\n支持的缺陷类型:")
        print("-" * 80)
        for defect in DEFECT_CATALOG:
            print(f"  {defect.defect_id:25} [{defect.severity:8}] - {defect.name}")
            print(f"    {defect.description}")
            print(f"    注入方法：{defect.injection_method}")
        print("-" * 80)
        return

    # 生成测试样本
    output_dir = Path(args.output_dir)
    generator = SampleGenerator(output_dir)

    print(f"生成测试样本到：{output_dir}")
    print("-" * 50)

    # 生成干净样本
    clean_path = generator.generate_clean_sample("clean_sample")
    print(f"[生成] 干净样本：{clean_path.name}")

    # 生成包含所有缺陷的样本
    if args.defect_types:
        defective_path, defects = generator.generate_defective_sample(
            name="defective_sample",
            defect_types=args.defect_types,
            seed=args.seed
        )
        print(f"[生成] 缺陷样本：{defective_path.name}")
        print(f"       注入缺陷数：{len(defects)}")
        for d in defects:
            print(f"         - {d['defect_id']}: {d['action']}")
    else:
        # 生成按类别分组的样本
        for category in ["A", "B", "D"]:
            defective_path, defects = generator.generate_defective_sample(
                name=f"defective_cat_{category}",
                defect_types=[category],
                seed=args.seed
            )
            print(f"[生成] Category {category} 缺陷样本：{defective_path.name}")
            print(f"       注入缺陷数：{len(defects)}")
            for d in defects:
                print(f"         - {d['defect_id']}: {d['action']}")

    print("-" * 50)
    print(f"样本生成完成。使用 --list-defects 查看所有支持的缺陷类型。")


if __name__ == "__main__":
    main()
