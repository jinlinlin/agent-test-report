"""规划器：决定下一步动作（LLM 规划推理）。

提供两种实现：
- DeterministicPlanner：脚本化取数顺序，无需 API Key 即可跑通链路（也用于测试与 V1-006 对照基线）。
- LLMPlanner：调用 OpenAI 兼容接口，让模型在工具集中自主选择下一步；解析失败回退到确定性规划。

动作类型：
- tool：调用某工具
- finish：结束（成功或失败由 State 决定）
- clarify：向用户澄清（缺版本名）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .state import AgentState

VERSION_PATTERN = re.compile(r"([A-Za-z\u4e00-\u9fa5]+\s*)?[Vv]?\d+(?:\.\d+)+")


@dataclass
class Action:
    type: str  # tool | finish | clarify
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    thought: str = ""


def extract_version_name(user_input: str) -> Optional[str]:
    """从自然语言输入中抽取版本名称；抽不到返回 None（触发澄清）。"""
    text = (user_input or "").strip()
    if not text:
        return None
    m = VERSION_PATTERN.search(text)
    if m:
        return m.group(0).strip()
    return None


class DeterministicPlanner:
    """按需求文档主链路固定顺序规划。"""

    name = "deterministic"

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg

    def _should_push(self, state: AgentState) -> bool:
        return bool(self.cfg and self.cfg.feishu.enabled
                    and state.feishu_doc_url is None and not state.feishu_error)

    def next_action(self, state: AgentState) -> Action:
        if state.version_name is None:
            return Action(type="clarify", thought="输入未识别到版本名称，需向用户澄清")

        if state.version_info is None:
            return Action(
                type="tool",
                tool="get_version_info",
                args={"version_name": state.version_name},
                thought="第一步：获取版本基础信息（描述、测试起止时间）",
            )

        if state.version_info.get("fetch_status") != "ok":
            return Action(type="finish", thought="版本信息获取失败，终止并输出失败说明版报告")

        if state.bugs is None:
            return Action(
                type="tool",
                tool="get_version_bugs",
                args={"version_name": state.version_name},
                thought="第二步：获取该版本全部缺陷明细",
            )

        if state.stats is None:
            return Action(type="tool", tool="compute_statistics", args={}, thought="第三步：调用独立统计工具计算指标")

        if state.report_markdown is None:
            return Action(type="tool", tool="render_report", args={}, thought="第四步：按章节结构渲染 Markdown 报告")

        # 第五步（可选）：报告已生成且开启飞书推送时，推送至飞书知识库
        if self._should_push(state):
            return Action(type="tool", tool="push_feishu", args={}, thought="第五步：推送报告至飞书云文档/知识库")

        return Action(type="finish", thought="报告已生成，结束")


class LLMPlanner:
    """让 LLM 在工具集中自主选择下一步；失败回退确定性规划。"""

    name = "llm"

    def __init__(self, llm_client, tool_specs, fallback: Optional[DeterministicPlanner] = None) -> None:
        self.llm = llm_client
        self.tool_specs = tool_specs
        self.fallback = fallback or DeterministicPlanner()

    def next_action(self, state: AgentState) -> Action:
        if state.version_name is None:
            return Action(type="clarify", thought="输入未识别到版本名称，需向用户澄清")
        try:
            prompt = self._build_prompt(state)
            raw = self.llm.complete(prompt)
            action = self._parse(raw)
            if action is not None:
                return action
        except Exception:
            pass
        return self.fallback.next_action(state)

    def _build_prompt(self, state: AgentState) -> str:
        tools = json.dumps(self.tool_specs, ensure_ascii=False)
        snap = json.dumps(state.snapshot(), ensure_ascii=False)
        return (
            "你是测试报告生成 Agent 的规划器。根据当前状态选择下一步动作，只输出 JSON。\n"
            f"可用工具：{tools}\n"
            f"当前状态：{snap}\n"
            "主链路顺序：get_version_info → get_version_bugs → compute_statistics → render_report → finish。\n"
            "取数失败则 finish。输出格式：{\"thought\": \"...\", \"type\": \"tool|finish\", \"tool\": \"工具名\", \"args\": {}}\n"
            "只输出 JSON，不要多余文字。"
        )

    def _parse(self, raw: str) -> Optional[Action]:
        text = (raw or "").strip()
        # 容错：截取第一个 { 到最后一个 }
        if "{" in text and "}" in text:
            text = text[text.index("{"): text.rindex("}") + 1]
        data = json.loads(text)
        atype = data.get("type", "tool")
        if atype not in ("tool", "finish"):
            return None
        return Action(
            type=atype,
            tool=data.get("tool", ""),
            args=data.get("args", {}) or {},
            thought=data.get("thought", ""),
        )


def build_planner(cfg, llm_client, tool_specs) -> Any:
    det = DeterministicPlanner(cfg)
    if cfg.llm.use_llm_planner and llm_client is not None and llm_client.available:
        return LLMPlanner(llm_client, tool_specs, fallback=det)
    return det
