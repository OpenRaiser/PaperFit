#!/usr/bin/env python3
"""候选修复价值评分（M3：候选不再只按生成顺序/静态 priority_score 执行）。

终版方案第五节：
  候选价值 = 缺陷消除收益 - 新缺陷风险 - 内容变化风险 - 修改范围
            - 目标定位不确定性 - 编译成本
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SEVERITY_WEIGHTS = {"critical": 100.0, "major": 60.0, "minor": 25.0, "unknown": 10.0}

# 家族 → 新缺陷风险系数（0=无新风险, 1=高）
FAMILY_NEW_DEFECT_RISK = {
    "B1": 0.4,  # 浮动体移动可能引起其他位移
    "B2": 0.3,
    "B3": 0.3,
    "B4": 0.5,  # longtable 转换风险较高
    "B5": 0.3,
    "A1": 0.1,
    "A2": 0.1,
    "A3": 0.4,  # 页数操作影响全局
    "A4": 0.2,
    "A5": 0.2,
    "A6": 0.1,
    "C1": 0.1,
    "C2": 0.0,  # 仅诊断
    "C3": 0.1,
    "C4": 0.1,
    "D1": 0.1,
    "D2": 0.3,  # 公式拆分可能改变视觉结构
    "D3": 0.05,
}

# 修改范围系数：局部对象 < 页级 < 全局
SCOPE_PENALTY = {"object": 0.0, "page": 10.0, "global": 25.0, "semantic_band": 5.0, "source_anchor": 5.0}

# 定位确定性：有 label > 有锚点 > 无锚
ANCHOR_CONFIDENCE = {"resolved": 0.0, "unresolved": 20.0, "page_level": 10.0}


def compute_candidate_value(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """计算候选的综合价值评分。"""
    family = str(candidate.get("defect_family") or "")[:2].upper()
    severity = str(candidate.get("severity") or "minor").lower()
    candidate_type = str(candidate.get("candidate_type") or "global")
    anchor_status = str(candidate.get("anchor_status") or "")
    requires_review = bool(candidate.get("requires_human_review"))
    confidence = float(candidate.get("visual_confidence") or candidate.get("confidence") or 0.5)

    # 1. 缺陷消除收益
    severity_score = SEVERITY_WEIGHTS.get(severity, 10.0)
    benefit = severity_score

    # 2. 新缺陷风险
    defect_risk = FAMILY_NEW_DEFECT_RISK.get(family, 0.3) * severity_score * 0.5

    # 3. 内容变化风险（语义编辑风险最大）
    content_risk = 0.0
    proposed_action = str(candidate.get("proposed_action") or "")
    if "semantic" in proposed_action.lower() or "text" in proposed_action.lower():
        content_risk = severity_score * 0.3
    if requires_review:
        content_risk += severity_score * 0.2

    # 4. 修改范围
    scope = SCOPE_PENALTY.get(candidate_type, 15.0)

    # 5. 定位不确定性
    anchor_penalty = ANCHOR_CONFIDENCE.get(anchor_status, 10.0)
    if not anchor_status:
        target = candidate.get("target") or {}
        if isinstance(target, dict) and target.get("label"):
            anchor_penalty = 0.0  # 有 label 隐含 resolved
        else:
            anchor_penalty = 10.0

    # 6. 编译成本（额外编译轮次估算）
    compile_cost = 5.0 if family in {"B4", "A3", "E1", "E2"} else 0.0

    # 置信度加权：低置信度降低收益
    if confidence < 0.6:
        benefit *= 0.5

    value = benefit - defect_risk - content_risk - scope - anchor_penalty - compile_cost

    return {
        "candidate_value": round(value, 2),
        "components": {
            "benefit": round(benefit, 2),
            "defect_risk": round(defect_risk, 2),
            "content_risk": round(content_risk, 2),
            "scope_penalty": round(scope, 2),
            "anchor_penalty": round(anchor_penalty, 2),
            "compile_cost": round(compile_cost, 2),
        },
        "rankable": not requires_review,
    }
