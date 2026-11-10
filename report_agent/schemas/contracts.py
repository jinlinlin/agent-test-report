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
class Summary:
    total: int = 0
    closed: int = 0
    pending_resolve: int = 0   # 待解决（active）
    pending_accept: int = 0    # 待验收（resolved/fixed）
    postponed: int = 0         # 延期
    fix_coverage: str = "0.00%"
    activated_ge1_count: int = 0
    activated_rate: str = "0.00%"


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
class StatsResult:
    version: VersionInfo = field(default_factory=VersionInfo)
    summary: Summary = field(default_factory=Summary)
    module_distribution: List[ModuleDist] = field(default_factory=list)
    level_status_distribution: List[LevelStatus] = field(default_factory=list)
    conclusion: Conclusion = field(default_factory=Conclusion)
    integrity: Integrity = field(default_factory=Integrity)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
