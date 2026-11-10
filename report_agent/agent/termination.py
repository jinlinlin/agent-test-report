"""终止条件判定（循环控制器的一部分）。

终止场景（需求文档 5.2）：
- 报告已生成 → finished
- 版本信息取数失败（不存在/超时/报错）→ failed
- 执行步数达上限 → step_limit
"""
from __future__ import annotations

from typing import Tuple

from .state import AgentState


def should_terminate(state: AgentState, max_steps: int, push_pending: bool = False) -> Tuple[bool, str]:
    # 报告已生成且无待推送的飞书动作 → finished（若 push_pending 则继续循环走 push_feishu）
    if state.report_markdown is not None and not push_pending:
        return True, "finished"

    # 版本信息已获取但失败：不再往下走，直接终止
    if state.version_info is not None and state.version_info.get("fetch_status") not in (None, "ok"):
        return True, "failed"

    if state.step >= max_steps:
        return True, "step_limit"

    return False, ""
