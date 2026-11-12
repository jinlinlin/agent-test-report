"""ReAct 最小循环：规划 → 工具调用 → 观察 → 更新 State → 终止判定。

组件职责清晰分离：
- planner 决定下一步（LLM 推理）
- registry 提供工具（取数/统计/渲染）
- state 承载短期记忆
- termination + max_steps 构成循环控制器
- tracer 逐轮记录
"""
from __future__ import annotations

from typing import Optional

from ..report.renderer import render_failure_report, render_step_limit_report
from ..tools.base import ToolRegistry, ToolResult
from .planner import Action, extract_version_name
from .state import AgentState
from .termination import should_terminate

FETCH_TOOLS = {"get_version_info", "get_version_bugs"}


class ReportAgent:
    def __init__(self, cfg, registry: ToolRegistry, planner, tracer) -> None:
        self.cfg = cfg
        self.registry = registry
        self.planner = planner
        self.tracer = tracer

    # ---- 对外入口 ----
    def run(self, user_input: str) -> AgentState:
        state = AgentState(user_input=user_input)
        state.version_name = extract_version_name(user_input)

        # 缺版本名：触发一次澄清（CLI 场景直接返回 clarify，由上层决定如何补全）
        if state.version_name is None:
            state.status = "clarify"
            state.fail_reason = "缺少版本名称"
            self.tracer.log(step=0, type="terminate", thought="输入未识别到版本名称",
                            fail_reason="clarify", user_input=user_input, state_snapshot=state.snapshot())
            return state

        self.tracer.log(step=0, type="llm_reasoning", thought=f"识别到版本名称：{state.version_name}",
                        user_input=user_input, state_snapshot=state.snapshot())

        max_steps = self.cfg.agent.max_steps
        while True:
            if state.status in ("failed", "finished", "step_limit", "clarify"):
                break

            terminate, reason = should_terminate(state, max_steps, push_pending=self._push_pending(state))
            if terminate:
                state.status = reason
                break

            action = self.planner.next_action(state)
            state.step += 1
            self.tracer.log(step=state.step, type="llm_reasoning", thought=action.thought,
                            tool_name=action.tool, tool_args=action.args, state_snapshot=state.snapshot())

            if action.type == "finish":
                state.status = "finished" if state.report_markdown is not None else "failed"
                break
            if action.type == "clarify":
                state.status = "clarify"
                break

            result = self._exec_tool(action, state)
            self.tracer.log(step=state.step, type="tool_call", tool_name=action.tool,
                            tool_args=action.args, tool_result=self._brief(result),
                            state_snapshot=state.snapshot())
            self._apply_result(action.tool, result, state)

        self._finalize(state, max_steps)
        self.tracer.log(step=state.step, type="terminate", thought=f"终止状态：{state.status}",
                        fail_reason=state.fail_reason, state_snapshot=state.snapshot())
        return state

    # ---- 工具执行 ----
    def _push_pending(self, state: AgentState) -> bool:
        """飞书已启用、报告已生成、且尚未推送成功/未失败 → 还需执行一步 push_feishu。"""
        return (self.cfg.feishu.enabled
                and state.report_markdown is not None
                and state.feishu_doc_url is None
                and not state.feishu_error)

    def _exec_tool(self, action: Action, state: AgentState) -> ToolResult:
        tool = self.registry.get(action.tool)
        if tool is None:
            return ToolResult(ok=False, error=f"未知工具：{action.tool}")

        kwargs = dict(action.args or {})
        # 由循环注入的依赖数据（规划器/LLM 无需搬运大对象）
        if action.tool == "get_version_info":
            kwargs["user_input"] = state.user_input
        elif action.tool == "get_version_bugs":
            kwargs["version_info"] = state.version_info
            kwargs["user_input"] = state.user_input
        elif action.tool == "compute_statistics":
            kwargs["version_info"] = state.version_info
            kwargs["bugs"] = state.bugs
        elif action.tool == "polish_conclusion":
            kwargs["stats_obj"] = state.stats_obj
        elif action.tool == "render_report":
            kwargs["stats_obj"] = state.stats_obj
            vi = state.version_info or {}
            kwargs["product_name"] = vi.get("product_name", "")
            kwargs["build"] = vi.get("build", "")
        elif action.tool == "push_feishu":
            kwargs["report_markdown"] = state.report_markdown
            kwargs["version_name"] = state.version_name or ""
            vi = state.version_info or {}
            kwargs["product_name"] = vi.get("product_name", "")
            kwargs["build"] = vi.get("build", "")
            kwargs["stats_obj"] = state.stats_obj

        try:
            return tool.run(**kwargs)
        except Exception as exc:  # 工具异常兜底，不让循环崩溃
            return ToolResult(ok=False, error=f"工具执行异常：{exc}")

    def _apply_result(self, tool_name: str, result: ToolResult, state: AgentState) -> None:
        # 飞书推送失败为非致命：记录原因，保留已生成的报告，不推翻主链路结果
        if tool_name == "push_feishu":
            if result.ok:
                state.feishu_doc_url = result.data
            else:
                state.feishu_error = result.error or "飞书推送失败"
            state.history.append({"tool": tool_name, "ok": result.ok})
            return

        if not result.ok:
            # 取数失败：如实标注，终止（防幻觉，不重试编造）
            state.status = "failed"
            state.fail_reason = result.error or "取数失败"
            return

        if tool_name == "get_version_info":
            state.version_info = result.data
        elif tool_name == "get_version_bugs":
            state.bugs = result.data or []
        elif tool_name == "compute_statistics":
            state.stats = result.data
            state.stats_obj = result.meta.get("stats_obj")
        elif tool_name == "polish_conclusion":
            # 润色结果已就地写回 stats_obj.conclusion.release_advice，仅记执行标记
            state.conclusion_polished = True
        elif tool_name == "render_report":
            state.report_markdown = result.data
            # 若还需推送飞书，则暂不置 finished，交由规划器再走一步 push_feishu
            if not self._push_pending(state):
                state.status = "finished"

        state.history.append({"tool": tool_name, "ok": result.ok})

    # ---- 终止收口 ----
    def _finalize(self, state: AgentState, max_steps: int) -> None:
        if state.status == "finished" and state.report_markdown is not None:
            return
        if state.status == "step_limit":
            state.report_markdown = render_step_limit_report(state.step, max_steps, state.version_name)
            return
        if state.status == "failed":
            reason = "获取禅道数据失败，无法完成缺陷统计"
            state.report_markdown = render_failure_report(reason, state.version_name, state.fail_reason)
            return
        # finished 但无报告（异常兜底）
        if state.report_markdown is None:
            state.status = "failed"
            state.report_markdown = render_failure_report(
                "报告生成失败", state.version_name, state.fail_reason or "未知原因")

    @staticmethod
    def _brief(result: ToolResult):
        """Trace 中工具返回的轻量摘要，避免把整份缺陷列表写进 Trace。"""
        if not result.ok:
            return {"ok": False, "error": result.error}
        data = result.data
        if isinstance(data, list):
            return {"ok": True, "count": len(data)}
        if isinstance(data, dict):
            keys = list(data.keys())
            return {"ok": True, "keys": keys}
        if isinstance(data, str):
            return {"ok": True, "length": len(data)}
        return {"ok": True}
