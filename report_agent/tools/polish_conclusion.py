"""F17 结论段 LLM 润色（验收 A9）。

在 F8 规则引擎的结构化结论之上，由 LLM 把 5.1 发布建议组织为面向项目组的自然语言段落。
硬约束（防幻觉立场，见 A4 对照实验反例）：
- LLM 仅组织语言，数字必须原样引用输入 evidence/advice；
- 工具层正则回抽润色文本中的全部数字与输入比对：不得引入新数字、不得丢失原结论关键数字；
- 校验不通过一律拒绝替换、回退模板原文；
- report.polish_conclusion 开关默认关，关闭时本工具不注册、链路行为与现状逐字一致。
"""
from __future__ import annotations

import re
from typing import Any, List, Set

from .base import Tool, ToolResult

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def extract_numbers(*texts: str) -> Set[str]:
    """回抽文本中出现的数字集合（整数与小写成一致字符串，比率如 5.00 与 5 区分保留原样）。"""
    found: Set[str] = set()
    for t in texts:
        for m in _NUM_RE.finditer(t or ""):
            found.add(m.group(0))
    return found


class PolishConclusionTool(Tool):
    name = "polish_conclusion"
    description = (
        "基于 compute_statistics 输出的结构化结论，由 LLM 将 5.1 发布建议润色为面向项目组的"
        "自然语言段落（仅组织语言，数字必须与统计结果完全一致，校验失败自动回退原文）。"
        "应在 compute_statistics 之后、render_report 之前调用；开关 report.polish_conclusion 关闭时不参与链路。"
    )
    parameters = {"type": "object", "properties": {}}

    SYSTEM_PROMPT = (
        "你是测试报告文案助手。请把给出的发布建议改写为更连贯、正式的面向项目组的自然语言段落。\n"
        "硬性约束：只能使用输入材料中已出现的数字，不得新增、删除或修改任何数字；"
        "不得引入任何新事实或新结论；只输出改写后的段落本身，不要任何多余文字。"
    )

    def __init__(self, cfg, llm_client: Any = None) -> None:
        self.cfg = cfg
        self.llm = llm_client

    def run(self, stats_obj: Any = None, **_) -> ToolResult:  # type: ignore[override]
        if stats_obj is None or getattr(stats_obj, "conclusion", None) is None:
            return ToolResult(ok=False, error="缺少统计结论对象")

        original = stats_obj.conclusion.release_advice or ""
        evidence: List[str] = list(stats_obj.conclusion.evidence or [])

        if self.llm is None or not getattr(self.llm, "available", False):
            return ToolResult(ok=True, data=original,
                              meta={"polished": False, "reason": "llm_unavailable"})

        prompt = (
            f"原发布建议：\n{original}\n\n"
            "发布准入判定依据（数字同样只能原样引用）：\n" + "\n".join(evidence)
        )
        try:
            polished = (self.llm.complete(prompt, system=self.SYSTEM_PROMPT) or "").strip()
        except Exception as exc:
            return ToolResult(ok=True, data=original,
                              meta={"polished": False, "reason": f"llm_error: {exc}"})

        # 数字一致性校验：不引入新数字、不丢失原建议数字
        allowed = extract_numbers(original, *evidence)
        required = extract_numbers(original)
        got = extract_numbers(polished)
        if not polished or not got <= allowed or not required <= got:
            return ToolResult(ok=True, data=original,
                              meta={"polished": False, "reason": "numbers_mismatch"})

        stats_obj.conclusion.release_advice = polished
        return ToolResult(ok=True, data=polished, meta={"polished": True})
