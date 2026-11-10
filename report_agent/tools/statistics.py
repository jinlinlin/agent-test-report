"""F3 缺陷统计汇总工具（独立统计能力，纯函数计算，禁止 LLM 参与）。

输入缺陷明细列表，输出结构化 StatsResult（第 7 章契约）：
总量、状态分布、模块分布、等级×状态交叉分布、修复覆盖率、激活率。
发布准入结论委托给 rules.release_engine（F8）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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


# 缺陷创建时间（zentao_bugs 规范化时写入的内部键）与北京时区
_OPENED_DATE_KEY = "openedDate"
_CN_TZ = timezone(timedelta(hours=8))
_NULLISH = {"", "null", "none", "0000-00-00 00:00:00", "0000-00-00"}


def _parse_dt(value: Any) -> Optional[datetime]:
    """解析禅道时间（ISO8601 UTC，如 2026-11-09T02:01:28Z）为带时区 datetime；无法解析返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _NULLISH:
        return None
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CN_TZ)  # 无时区按禅道服务器北京时间处理
    return dt


def opened_date_range(bugs: List[Dict[str, Any]]) -> tuple:
    """返回本版本缺陷创建时间的 (最早, 最晚)，按北京时间 YYYY-MM-DD；无可用日期时返回 ('', '')。"""
    dates = [d for d in (_parse_dt(b.get(_OPENED_DATE_KEY)) for b in bugs) if d is not None]
    if not dates:
        return "", ""
    lo, hi = min(dates), max(dates)
    return lo.astimezone(_CN_TZ).strftime("%Y-%m-%d"), hi.astimezone(_CN_TZ).strftime("%Y-%m-%d")


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

    # 测试起止时间：取本版本全部缺陷「创建时间(openedDate)」的最早/最晚（用户口径）；
    # 能算出来就用它，否则回退到上游 version_info 传入的配置/占位值。
    start, end = opened_date_range(bugs)
    if start and end:
        vi.test_start, vi.test_end = start, end

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
