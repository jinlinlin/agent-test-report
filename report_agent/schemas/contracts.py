"""统计工具输出契约（对应需求文档第 7 章 JSON Schema）。

设计原则：F3 统计工具产出结构化数据，F4 渲染只做「数据 → 模板」填充，LLM 不参与任何计算。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class VersionInfo:
    name: str = ""
    description: str = ""
    test_start: str = ""
    test_end: str = ""
    fetch_status: str = "ok"  # ok | not_found | timeout | error


@dataclass
class ActivatedBucket:
    """4.2 激活次数逐档：activatedCount=某次数的缺陷个数（含 0 次未激活）。"""
    times: int
    total: int = 0


@dataclass
class Summary:
    total: int = 0
    closed: int = 0
    pending_resolve: int = 0   # 待解决（active）
    pending_accept: int = 0    # 待验收（resolved/fixed）
    postponed: int = 0         # 延期
    fix_coverage: str = "0.00%"
    activated_ge1_count: int = 0
    activated_rate: str = "0.00%"
    activated_dist: List[ActivatedBucket] = field(default_factory=list)  # 4.2 逐档分布（按激活次数）


@dataclass
class ModuleDist:
    module: str
    total: int = 0
    closed: int = 0
    pending_resolve: int = 0
    pending_accept: int = 0
    postponed: int = 0


@dataclass
class LevelStatus:
    level: str  # P1 | P2 | P3 | P4
    total: int = 0
    active: int = 0            # 激活（待解决），发布准入分子
    closed: int = 0
    pending_accept: int = 0
    postponed: int = 0


@dataclass
class Conclusion:
    gate_basis: str = "active_ratio"
    p1_active: int = 0
    p1_pass: bool = True
    p2_active: int = 0
    p2_active_ratio: str = "0.00%"
    p2_pass: bool = True
    p3_active: int = 0
    p3_active_ratio: str = "0.00%"
    p3_pass: bool = True
    p4_active: int = 0
    p4_limited: bool = False
    release_advice: str = ""
    evidence: List[str] = field(default_factory=list)


@dataclass
class Integrity:
    data_complete: bool = True
    missing_fields: List[str] = field(default_factory=list)


@dataclass
class PersonCount:
    """4.3/4.4 每人计数：按缺陷 openedBy/resolvedBy 实算聚合，按件数降序。"""
    name: str
    total: int = 0


@dataclass
class People:
    """3.1 测试人员（由本版本缺陷 openedBy/resolvedBy 如实聚合；无数据则留空待补充）。"""
    project_owner: str = ""              # 项目负责人（需禅道项目接口，未实测前留空）
    test_lead: List[str] = field(default_factory=list)   # 测试负责人（=本版本提交人）
    developers: List[str] = field(default_factory=list)  # 软件工程师（=本版本解决人）
    testers: List[str] = field(default_factory=list)     # 测试人员（=本版本提交人）
    submit_counts: List[PersonCount] = field(default_factory=list)   # 4.3 每人提交数（openedBy）
    resolve_counts: List[PersonCount] = field(default_factory=list)  # 4.4 每人解决数（resolvedBy，仅计有解决人的缺陷）


@dataclass
class StatsResult:
    version: VersionInfo = field(default_factory=VersionInfo)
    summary: Summary = field(default_factory=Summary)
    module_distribution: List[ModuleDist] = field(default_factory=list)
    level_status_distribution: List[LevelStatus] = field(default_factory=list)
    conclusion: Conclusion = field(default_factory=Conclusion)
    integrity: Integrity = field(default_factory=Integrity)
    people: People = field(default_factory=People)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
