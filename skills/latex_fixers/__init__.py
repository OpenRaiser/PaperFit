"""
PaperFit LaTeX Fixers - 可执行的 LaTeX 排版修复函数库

本模块将 SKILL.md 中的修复策略转化为可执行、可测试的 Python 函数。
供 code-surgeon-agent 直接调用，实现自动化修复。
"""

from .overflow_fixers import (
    fix_overfull_hbox,
    fix_paragraph_overflow,
    fix_table_overflow,
    fix_long_formula,
    fix_url_overflow,
)
from .float_fixers import (
    fix_float_placement,
    fix_float_width,
    fix_float_fullwidth,
    fix_float_clustering,
    fix_split_float,
    fix_table_width,
)
from .space_util_fixers import (
    fix_widow_orphan,
    fix_trailing_whitespace,
    fix_page_budget,
    fix_unbalanced_columns,
)
from .fullwidth_fixers import (
    fix_figure_fullwidth,
    fix_table_fullwidth,
    fix_all_floats_fullwidth,
    ensure_reference_newpage,
    fix_body_last_page,
)
from .semantic_micro_tuning import (
    minimalist_shorten,
    deep_expand,
    semantic_intervention,
)
from .utils import (
    add_package_to_preamble,
    add_to_preamble,
    find_paragraph_start,
    find_paragraph_end,
)

__all__ = [
    # Overflow fixes
    "fix_overfull_hbox",
    "fix_paragraph_overflow",
    "fix_table_overflow",
    "fix_long_formula",
    "fix_url_overflow",
    # Float fixes
    "fix_float_placement",
    "fix_float_width",
    "fix_float_fullwidth",
    "fix_float_clustering",
    "fix_split_float",
    "fix_table_width",
    # Full-width fixes (absolute priority)
    "fix_figure_fullwidth",
    "fix_table_fullwidth",
    "fix_all_floats_fullwidth",
    "ensure_reference_newpage",
    "fix_body_last_page",
    # Space utilization fixes
    "fix_widow_orphan",
    "fix_trailing_whitespace",
    "fix_page_budget",
    "fix_unbalanced_columns",
    # Utilities
    "add_package_to_preamble",
    "add_to_preamble",
    "find_paragraph_start",
    "find_paragraph_end",
    # Semantic micro-tuning
    "minimalist_shorten",
    "deep_expand",
    "semantic_intervention",
]
