"""规划器：决定下一步动作。

提供两种实现：
- DeterministicPlanner：脚本化取数顺序，无需 API Key 即可跑通链路（也用于测试与 V1-006 对照基线）。
- NativeFCPlanner：使用原生 Function Calling 协议（tools 参数 + tool_calls 解析 + role:tool 回传），
  模型自主决定调用哪个工具与参数；失败回退到确定性规划。

动作类型：
- tool：调用某工具
- finish：结束（成功或失败由 State 决定）
- clarify：向用户澄清（缺版本名）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


class NativeFCPlanner:
    """原生 Function Calling 协议规划器。

    与旧的 prompt-based 规划方式的核心区别：
    - 工具定义通过 API 的 tools 参数传入（非拼接到 prompt 文本）
    - 模型返回结构化 tool_calls（不需要自己 json.loads 解析）
    - 维护多轮消息历史，工具结果以 role="tool" 回传模型（而非每轮重建 state.snapshot）
    - 不告诉模型执行顺序，由模型自己从工具描述推断下一步
    """

    name = "native_fc"

    def __init__(self, llm_client, tool_specs, fallback: Optional[DeterministicPlanner] = None) -> None:
        self.llm = llm_client
        self.tool_specs = tool_specs
        self.fallback = fallback or DeterministicPlanner()
        # 原生 FC 协议转换：把 Tool.spec() 输出转为 OpenAI tools 格式
        self._tools = [
            {"type": "function", "function": spec}
            for spec in tool_specs
        ]
        self._messages: List[Dict[str, Any]] = []
        self._initialized = False
        self._last_tool_call_id: str = ""
        self._last_tool_name: str = ""

    def next_action(self, state: AgentState) -> Action:
        if state.version_name is None:
            return Action(type="clarify", thought="输入未识别到版本名称，需向用户澄清")

        try:
            if not self._initialized:
                self._init_messages(state)
                self._initialized = True
            else:
                # 把上一步工具执行结果以 role="tool" 回传模型（这就是原生 FC 的 observation 机制）
                self._append_tool_observation(state)

            # 调用模型：不告知执行顺序，模型自主决定下一步
            msg = self.llm.complete_with_tools(self._messages, self._tools)
            self._messages.append(msg)  # 把模型响应加入历史

            # 解析响应：有 tool_calls → 继续执行工具；无 tool_calls → 任务完成
            if msg.tool_calls:
                tc = msg.tool_calls[0]  # 取第一个（单次工具调用模式）
                self._last_tool_call_id = tc.id
                self._last_tool_name = tc.function.name
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                thought = msg.content or f"模型选择调用 {tc.function.name}"
                return Action(type="tool", tool=tc.function.name, args=args, thought=thought)
            else:
                # 模型认为任务完成，返回最终回答
                return Action(type="finish", thought=msg.content or "模型判断任务完成")

        except Exception as exc:
            # 原生 FC 失败时回退到确定性规划（保证生产可用性）
            return self.fallback.next_action(state)

    def _init_messages(self, state: AgentState) -> None:
        """初始化对话历史：系统提示 + 用户目标。不包含执行顺序提示！"""
        self._messages = [
            {
                "role": "system",
                "content": (
                    "你是测试报告生成 Agent。你的目标是根据用户请求生成测试报告。\n"
                    "你可以调用以下工具完成任务：获取版本信息、获取缺陷列表、计算统计、渲染报告。\n"
                    "每次只调用一个最合适的工具，等待结果后再决定下一步。\n"
                    "当报告已生成后，输出最终回答表示任务完成。"
                ),
            },
            {
                "role": "user",
                "content": f"请为版本 {state.version_name} 生成测试报告。",
            },
        ]

    def _append_tool_observation(self, state: AgentState) -> None:
        """把上一步工具执行的结果以 role="tool" 消息回传给模型。

        这是原生 FC 协议的关键环节：模型看到 observation 后自主决定下一步。
        """
        # 从 state 中提取上一步工具的结果数据
        result_data = self._extract_result_for_tool(self._last_tool_name, state)
        # 截断过长的结果（避免超 token）
        content = json.dumps(result_data, ensure_ascii=False, default=str)[:3000]
        self._messages.append({
            "role": "tool",
            "tool_call_id": self._last_tool_call_id,
            "content": content,
        })

    @staticmethod
    def _extract_result_for_tool(tool_name: str, state: AgentState):
        """根据工具名从 State 提取对应的执行结果。"""
        if tool_name == "get_version_info":
            return state.version_info
        elif tool_name == "get_version_bugs":
            # 缺陷列表可能很长，只返回摘要给模型
            if state.bugs is not None:
                return {"count": len(state.bugs), "sample": state.bugs[:3] if state.bugs else []}
            return None
        elif tool_name == "compute_statistics":
            return state.stats
        elif tool_name == "render_report":
            return {"ok": state.report_markdown is not None, "length": len(state.report_markdown or "")}
        elif tool_name == "push_feishu":
            return {"url": state.feishu_doc_url, "error": state.feishu_error}
        return {"done": True}


def build_planner(cfg, llm_client, tool_specs) -> Any:
    """根据配置组装规划器。

    use_native_fc=true 且有 API Key → NativeFCPlanner（原生 Function Calling）
    否则 → DeterministicPlanner（确定性脚本化规划）
    """
    det = DeterministicPlanner(cfg)
    if cfg.llm.use_native_fc and llm_client is not None and llm_client.available:
        return NativeFCPlanner(llm_client, tool_specs, fallback=det)
    return det
