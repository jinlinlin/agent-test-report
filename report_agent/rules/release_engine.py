"""F8 发布建议规则引擎（确定性判定，非 LLM 推理）。

准入口径（需求文档 3.3）：分子=激活(active/待解决)缺陷数，分母=缺陷总数，占比两位小数。
- P1 致命：激活数必须为 0
- P2 严重：激活占比 ≤ 5%
- P3 一般：激活占比 ≤ 30%
- P4 建议：不做限制
"""
from __future__ import annotations

from typing import List

from ..schemas.contracts import Conclusion, LevelStatus

P2_THRESHOLD = 5.00
P3_THRESHOLD = 30.00


def _ratio_pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _by_level(dist: List[LevelStatus]) -> dict:
    return {ls.level: ls for ls in dist}


def evaluate(level_status_distribution: List[LevelStatus], total: int) -> Conclusion:
    m = _by_level(level_status_distribution)
    p1 = m.get("P1", LevelStatus(level="P1"))
    p2 = m.get("P2", LevelStatus(level="P2"))
    p3 = m.get("P3", LevelStatus(level="P3"))
    p4 = m.get("P4", LevelStatus(level="P4"))

    p2_ratio = _ratio_pct(p2.active, total)
    p3_ratio = _ratio_pct(p3.active, total)

    p1_pass = p1.active == 0
    p2_pass = p2_ratio <= P2_THRESHOLD
    p3_pass = p3_ratio <= P3_THRESHOLD

    evidence: List[str] = [
        f"激活 P1={p1.active}（门槛：=0）{'，达标' if p1_pass else '，未达标'}",
        f"激活 P2 占比 {p2_ratio:.2f}%（门槛：≤{P2_THRESHOLD:.2f}%）{'，达标' if p2_pass else '，未达标'}",
        f"激活 P3 占比 {p3_ratio:.2f}%（门槛：≤{P3_THRESHOLD:.2f}%）{'，达标' if p3_pass else '，未达标'}",
        f"激活 P4={p4.active}（不做限制）",
    ]

    if total == 0:
        advice = "本版本未发现缺陷，符合上线准入标准，可按计划发布。"
    elif p1_pass and p2_pass and p3_pass:
        advice = "各项准入指标均达标，符合上线准入标准，可按计划发布。"
    else:
        failed = []
        if not p1_pass:
            failed.append(f"P1 存在 {p1.active} 个激活缺陷（须为 0）")
        if not p2_pass:
            failed.append(f"P2 激活占比 {p2_ratio:.2f}% 超过 {P2_THRESHOLD:.2f}%")
        if not p3_pass:
            failed.append(f"P3 激活占比 {p3_ratio:.2f}% 超过 {P3_THRESHOLD:.2f}%")
        advice = "存在未达准入标准项，建议暂缓发布：" + "；".join(failed) + "。"

    return Conclusion(
        gate_basis="active_ratio",
        p1_active=p1.active,
        p1_pass=p1_pass,
        p2_active=p2.active,
        p2_active_ratio=f"{p2_ratio:.2f}%",
        p2_pass=p2_pass,
        p3_active=p3.active,
        p3_active_ratio=f"{p3_ratio:.2f}%",
        p3_pass=p3_pass,
        p4_active=p4.active,
        p4_limited=False,
        release_advice=advice,
        evidence=evidence,
    )
