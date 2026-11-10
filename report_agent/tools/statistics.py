"""F3 缺陷统计汇总工具（独立统计能力，纯函数计算，禁止 LLM 参与）。

输入缺陷明细列表，输出结构化 StatsResult（第 7 章契约）：
总量、状态分布、模块分布、等级×状态交叉分布、修复覆盖率、激活率。
发布准入结论委托给 rules.release_engine（F8）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..rules.release_engine import evaluate
from ..schemas.contracts import (
    Conclusion,
    Integrity,
    LevelStatus,
    ModuleDist,
    StatsResult,
    Summary,
    VersionInfo,
)
from .base import Tool, ToolResult

LEVEL_MAP = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}
LEVELS = ["P1", "P2", "P3", "P4"]
# 延期仅认“延期处理(postponed)”；其余 resolution（fixed/willnotfix/bydesign/duplicate…）不算延期
POSTPONED_RESOLUTIONS = {"postponed"}

# 状态桶：closed 已关闭 / pending_accept 待验收 / pending_resolve 待解决(激活) / postponed 延期
BUCKETS = ["closed", "pending_accept", "pending_resolve", "postponed"]


def level_of(severity: Any) -> str:
    try:
        return LEVEL_MAP.get(int(severity), "P3")
    except (TypeError, ValueError):
        return "P3"


def status_bucket(bug: Dict[str, Any]) -> str:
    """按 3.2 状态映射表归类（互斥，优先级自上而下）：
    1) 已关闭：status=closed（无论 resolution 为何）
    2) 待解决：status=active
    3) 延期：status≠closed 且 resolution=延期处理(postponed)
    4) 待验收：status=resolved 且 resolution≠延期处理
    """
    status = str(bug.get("status") or "").lower()
    res = str(bug.get("resolution") or "").lower()
    if status == "closed":
        return "closed"
    if status == "active":
        return "pending_resolve"
    # 剩余为 resolved（或未知状态）
    if res in POSTPONED_RESOLUTIONS:
        return "postponed"
    if status == "resolved":
        return "pending_accept"
    return "pending_resolve"  # 未知状态兜底计入待解决


def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00%"
    return f"{round(numerator / denominator * 100, 2):.2f}%"


def compute_statistics(version_info: Dict[str, Any] | None, bugs: List[Dict[str, Any]] | None) -> StatsResult:
    bugs = bugs or []
    version_info = version_info or {}

    vi = VersionInfo(
        name=version_info.get("name", ""),
        description=version_info.get("description", ""),
        test_start=version_info.get("test_start", ""),
        test_end=version_info.get("test_end", ""),
        fetch_status=version_info.get("fetch_status", "ok"),
    )

    total = len(bugs)
    bucket_total = {b: 0 for b in BUCKETS}
    activated_ge1 = 0

    module_map: Dict[str, ModuleDist] = {}
    level_map: Dict[str, LevelStatus] = {lv: LevelStatus(level=lv) for lv in LEVELS}

    for bug in bugs:
        bucket = status_bucket(bug)
        bucket_total[bucket] += 1

        try:
            if int(bug.get("activatedCount", 0)) >= 1:
                activated_ge1 += 1
        except (TypeError, ValueError):
            pass

        mod = bug.get("module") or "未分类"
        if mod not in module_map:
            module_map[mod] = ModuleDist(module=mod)
        md = module_map[mod]
        md.total += 1
        setattr(md, bucket, getattr(md, bucket) + 1)

        lv = level_of(bug.get("severity"))
        ls = level_map[lv]
        ls.total += 1
        if bucket == "pending_resolve":
            ls.active += 1
        elif bucket == "closed":
            ls.closed += 1
        elif bucket == "pending_accept":
            ls.pending_accept += 1
        elif bucket == "postponed":
            ls.postponed += 1

    summary = Summary(
        total=total,
        closed=bucket_total["closed"],
        pending_resolve=bucket_total["pending_resolve"],
        pending_accept=bucket_total["pending_accept"],
        postponed=bucket_total["postponed"],
        fix_coverage=_pct(bucket_total["closed"], total),
        activated_ge1_count=activated_ge1,
        activated_rate=_pct(activated_ge1, total),
    )

    module_distribution = sorted(module_map.values(), key=lambda m: m.total, reverse=True)
    level_status_distribution = [level_map[lv] for lv in LEVELS]

    conclusion: Conclusion = evaluate(level_status_distribution, total)

    missing: List[str] = []
    if not vi.test_start or not vi.test_end:
        # 测试起止时间缺失仅作软性缺口标注，不阻断结论判定
        # （发布结论由缺陷统计推导，与时间字段无关）
        missing.append("测试起止时间")
    # data_complete 只取决于缺陷数据是否取数成功，时间缺失不影响结论输出
    integrity = Integrity(data_complete=(vi.fetch_status == "ok"), missing_fields=missing)

    return StatsResult(
        version=vi,
        summary=summary,
        module_distribution=module_distribution,
        level_status_distribution=level_status_distribution,
        conclusion=conclusion,
        integrity=integrity,
    )


class ComputeStatisticsTool(Tool):
    name = "compute_statistics"
    description = "对已获取的缺陷明细执行独立统计，输出结构化指标（总量/状态/模块/等级/覆盖率）与发布准入结论。由循环注入 version_info 与 bugs。"
    parameters = {"version_info": "object(注入)", "bugs": "array(注入)"}

    def run(self, version_info: Dict[str, Any] | None = None, bugs: List[Dict[str, Any]] | None = None, **kwargs) -> ToolResult:
        result = compute_statistics(version_info, bugs)
        return ToolResult(ok=True, data=result.to_dict(), meta={"stats_obj": result})
