"""短期记忆 State：贯穿整条链路的状态载体。

Agent 每一步读取/更新 State；Trace 逐轮快照 State 内容（需求文档第 9 章 state_snapshot）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentState:
    user_input: str = ""
    version_name: Optional[str] = None       # 从输入抽取；为 None 触发澄清
    clarify_count: int = 0

    plan: List[str] = field(default_factory=list)
    version_info: Optional[Dict[str, Any]] = None
    bugs: Optional[List[Dict[str, Any]]] = None
    stats: Optional[Dict[str, Any]] = None
    stats_obj: Optional[Any] = None          # StatsResult 对象，供渲染直接使用
    report_markdown: Optional[str] = None

    feishu_doc_url: Optional[str] = None     # 飞书推送成功后的文档链接（V2）
    feishu_error: str = ""                   # 飞书推送失败原因（非致命，不推翻报告）

    step: int = 0
    status: str = "running"                  # running | finished | failed | clarify | step_limit
    fail_reason: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)  # 已执行动作摘要

    def snapshot(self) -> Dict[str, Any]:
        """供 Trace 记录的轻量快照（不含大列表明细，只记规模）。"""
        return {
            "step": self.step,
            "status": self.status,
            "version_name": self.version_name,
            "has_version_info": self.version_info is not None,
            "bugs_count": len(self.bugs) if self.bugs is not None else None,
            "has_stats": self.stats is not None,
            "has_report": self.report_markdown is not None,
            "feishu_doc_url": self.feishu_doc_url,
            "fail_reason": self.fail_reason,
        }
