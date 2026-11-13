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

# 半截版本号：项目名后只跟一个数字段（如 "V5"、"5.0"），用于区分“不完整”与“缺失”。
# 注意：全版本号（≥两段点分，如 V5.0.0）由 FULL_BUILD 识别，不走此宽松匹配。
PARTIAL_VERSION = re.compile(r"[Vv]?\d+(?:\.\d+)?")

# 全版本 token（≥两段点分）：不带 VERSION_PATTERN 的字母/中文前缀组，
# 避免把项目词一并吞进匹配（否则会与 product 拼接时重复）。
FULL_BUILD = re.compile(r"[Vv]?\d+(?:\.\d+)+")

# 相对版本词：原始输入里表达「最新/上一个/新的」的措辞（无具体版本号时的语义线索）。
# 口径不变：词只映射到候选集序号（0=最新，1=上一个），候选排序仍由程序钉死。
# 注意「上个」系必须连写，避免「最新版本」被误判为「上一个版本」。
REL_VERSION_WORDS = re.compile(
    r"上一个|上一版|上版|上个版本|上个版|previous|"
    r"最新|最近|latest|newest|新的"
)


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


@dataclass
class Intent:
    """V2-1 模糊输入三态分类结果（项目名与版本号各自独立判定）。"""
    kind: str                    # "complete" | "incomplete" | "missing"
    product_hint: str = ""       # 命中的项目关键字（如 "UniResearch"）
    version_hint: str = ""       # 抽到的版本号原文（完整或半截）
    resolved_version: str = ""   # 完整态时直接给出的确定全名候选
    rel_hint: int = -1           # 相对版本词命中的候选序号（0=最新，1=上一个）；-1 未命中


def classify_input(user_input: str, known_products: List[str]) -> Intent:
    """纯规则、不调模型：项目名与版本号各自独立判定，返回三态。

    - 项目名：在 known_products 里做「先精确（子串）后包含」匹配（对齐 F12 口径）；
    - 版本号：VERSION_PATTERN 命中全名→完整；只有 PARTIAL 片段→不完整；无→缺版本。
    开关 agent.interactive_clarify 默认关时 loop 不会调用本函数，行为与 V1 逐字一致。
    """
    text = (user_input or "").strip()

    # 1) 项目判定（先精确子串；再用「文本词是项目名前缀」兼容简称，如「一表通」→「一表通项目」）
    product = ""
    tl = text.lower()
    for p in known_products:
        if p and p.lower() in tl:
            product = p
            break
    if not product:
        # known_products 已按长度降序，长名（最具体）优先，避免短名误抢
        for p in known_products:
            pl = p.lower()
            if not pl:
                continue
            for tok in re.findall(r"[A-Za-z\u4e00-\u9fa5]+", text):
                if len(tok) >= 2 and pl.startswith(tok.lower()):
                    product = p
                    break
            if product:
                break

    # 2) 版本判定（独立于项目；用 FULL_BUILD 只提版本 token，不含项目词）
    full = FULL_BUILD.search(text)              # 如 V5.0.0（≥两段）
    partial = PARTIAL_VERSION.search(text)       # 如 V5（单段）
    version = full.group(0).strip() if full else (partial.group(0).strip() if partial else "")

    # 3) 相对版本词：仅在没有全版本号时才有意义（有全名时它优先，措辞里的「最新」只是赘词）
    rel = -1
    if not full:
        m = REL_VERSION_WORDS.search(text)
        if m:
            rel = 1 if ("上" in m.group(0) or "previous" in m.group(0)) else 0

    # 4) 三态汇总：项目+全版本→完整；有任一线索（项目/版本片段/相对词）但不构成完整→不完整；全无线索→缺失
    if full and product:
        return Intent("complete", product, version, f"{product} {version}")
    if product or full or partial or rel >= 0:
        return Intent("incomplete", product, version, rel_hint=rel)
    return Intent("missing")                     # 无项目也无版本数字→先问项目


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

        # 第三步半（可选，F17）：开关开启时在渲染前润色 5.1 发布建议（工具内含数字一致性校验）
        if (self.cfg is not None and self.cfg.report.polish_conclusion
                and not state.conclusion_polished):
            return Action(type="tool", tool="polish_conclusion", args={},
                          thought="第三步半：由 LLM 润色发布建议（数字原样引用，校验失败回退模板）")

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

        # 确定性推送守卫：开启飞书推送是用户指令而非模型裁量。
        # 条件与 loop._push_pending 对齐：已开启 + 报告已生成 + 尚未推送成功且无错误。
        # 缺少该守卫时，模型可能在 render 后直接返回 finish，导致 --push-feishu 时灵时不灵。
        fcfg = getattr(self.fallback, "cfg", None)
        if (fcfg is not None and fcfg.feishu.enabled
                and state.report_markdown is not None
                and state.feishu_doc_url is None and not state.feishu_error):
            return Action(type="tool", tool="push_feishu", args={},
                          thought="用户已开启飞书推送且尚未完成，确定性执行（非模型裁量）")

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
        elif tool_name == "polish_conclusion":
            return {"polished": state.conclusion_polished}
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
