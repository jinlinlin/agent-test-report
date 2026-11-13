"""ReAct 最小循环：规划 → 工具调用 → 观察 → 更新 State → 终止判定。

组件职责清晰分离：
- planner 决定下一步（LLM 推理）
- registry 提供工具（取数/统计/渲染）
- state 承载短期记忆
- termination + max_steps 构成循环控制器
- tracer 逐轮记录
"""
from __future__ import annotations

from typing import Dict, Optional

from ..report.renderer import render_failure_report, render_step_limit_report
from ..tools.base import ToolRegistry, ToolResult
from .planner import Action, classify_input, extract_version_name
from .state import AgentState
from .termination import should_terminate

FETCH_TOOLS = {"get_version_info", "get_version_bugs"}


class ReportAgent:
    def __init__(self, cfg, registry: ToolRegistry, planner, tracer, ask=None, llm=None) -> None:
        self.cfg = cfg
        self.registry = registry
        self.planner = planner
        self.tracer = tracer
        # 交互澄清回调：ask(question, options) -> 用户答复字符串。为 None 时无法交互，
        # 仅在 cfg.agent.interactive_clarify 开启且输入歧义时使用（CLI 接 input()、测试注入脚本化答复）。
        self.ask = ask
        # LLM 兜底翻译（llm_intent_fallback 开启才用）：规则不命中时把原始输入映射到程序给的候选集，
        # 模型只翻译不造事实；默认关时无 LLM 也能全链路跑通（离线可跑约束不变）。
        self.llm = llm
        # V2-3 项目级参考文档链接记忆小抄（doc_links_enabled 开启才惰性创建；关闭时永不触碰文件）
        self._doc_store_obj = None

    # ---- 对外入口 ----
    def run(self, user_input: str) -> AgentState:
        state = AgentState(user_input=user_input)

        if self.cfg.agent.interactive_clarify:
            # V2-1 交互澄清（仅开关开启时进入）：三态识别 + 候选反问 + 至多 2 回合
            state = self._resolve_with_clarify(state)
            if state.status == "clarify":
                self.tracer.log(step=0, type="terminate", thought="输入歧义，需用户澄清",
                                fail_reason="clarify", user_input=user_input, state_snapshot=state.snapshot())
                return state
        else:
            # 关闭时与 V1 逐字一致：二态抽取，抽不到直接一次性返回 clarify
            state.version_name = extract_version_name(user_input)
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

    # ---- V2-1 交互澄清（仅 interactive_clarify 开启时被 run() 调用）----
    def _known_products(self) -> list:
        """已知项目名：mock 取本地表；live 拉 /products（异常回退 default_product）。"""
        if self.cfg.zentao.mode == "mock":
            from ..tools import mock_zentao
            return mock_zentao.list_products()
        try:
            from ..tools import zentao_client as zc
            client = zc.ZentaoRestClient(self.cfg)
            names = {str(p.get(self.cfg.zentao.field_product_name, "")).strip() for p in client._all_products()}
            names.discard("")
            if names:
                return sorted(names, key=len, reverse=True)
        except Exception:
            pass
        return [self.cfg.zentao.default_product] if self.cfg.zentao.default_product else []

    def _candidates(self, product_hint: str) -> list:
        """调候选工具取该项目版本候选（已按创建时间倒序，最新在前）。"""
        tool = self.registry.get("list_version_candidates")
        if tool is None:
            return []
        res = tool.run(product_hint=product_hint)
        return res.data.get("candidates", []) if (res.ok and isinstance(res.data, dict)) else []

    @staticmethod
    def _resolve_product(answer: str, products: list) -> str:
        """把用户对项目的自然语言答复映射为确定项目名（序号/包含匹配）。"""
        a = (answer or "").strip()
        la = a.lower()
        if a.isdigit():
            i = int(a) - 1
            return products[i] if 0 <= i < len(products) else ""
        for p in products:
            if p.lower() in la:
                return p
        return ""

    @staticmethod
    def _resolve_version(answer: str, cands: list) -> str:
        """把用户对版本的自然语言答复映射为确定版本全名。

        “最新的”→cands[0]（已按创建时间倒序，程序钉死）；序号→第 i 个；否则按 build/名称包含匹配。
        """
        a = (answer or "").strip()
        la = a.lower()
        if a.isdigit():
            i = int(a) - 1
            return cands[i]["name"] if 0 <= i < len(cands) else ""
        if "最新" in a or la in ("latest", "new", "last", "最近"):
            return cands[0]["name"] if cands else ""
        for c in cands:
            if c.get("build") and c["build"].lower() in la:
                return c["name"]
        return ""

    # ---- V2-1 FC 话术子项（仅原生 FC 模式启用）----
    def _fc_clarify_active(self) -> bool:
        """原生 FC 澄清子项开关：use_native_fc 且 LLM 可用才启用；否则话术/解析均走现有模板+规则（逐字不变）。"""
        return bool(getattr(self.cfg.llm, "use_native_fc", False)
                    and self.llm is not None and getattr(self.llm, "available", False))

    def _fc_question(self, base_question: str, options: list) -> str:
        """FC 模式下由模型组织澄清话术（候选选项仍由程序给定，模型只负责措辞）；非 FC/失败回退模板。"""
        if not self._fc_clarify_active():
            return base_question
        prompt = (
            f"需要向用户澄清，以便从以下候选中选出一个：{options}\n"
            f"参考问法：{base_question}\n"
            "请用一两句自然中文重新组织友好的询问话术，不要替用户选择、不要删改或编造候选。"
        )
        try:
            q = (self.llm.complete(
                prompt, system="你是测试报告 Agent 的澄清话术生成器，只负责措辞询问。") or "").strip()
        except Exception:
            return base_question
        return q or base_question

    def _fc_parse_answer(self, answer: str, options: list) -> str:
        """FC 模式下由模型把用户对澄清的自然语言答复映射为候选集内的确定项；严格校验在集内，
        越界/失败/None 返回空串交调用方回退规则解析（防幻觉，不编造）。"""
        if not self._fc_clarify_active() or not options:
            return ""
        a = (answer or "").strip()
        if not a:
            return ""
        prompt = (
            f"用户对下列候选的答复是：{a}\n候选（仅能从中选一个）：{options}\n"
            "若能确定用户指向的唯一候选，只输出该候选原文；不确定或无法对应则只输出 None。禁止编造候选之外的内容。"
        )
        try:
            out = (self.llm.complete(
                prompt, system="你是答复解析器，只能从给定候选中原样选择，禁止编造。") or "").strip()
        except Exception:
            return ""
        if not out or out.lower() == "none":
            return ""
        ol = out.lower()
        for o in options:
            # 只接受精确命中或模型用整句包裹了候选（候选是输出子串）；
            # 不接受「输出是候选子串」（残片会误命中，防幻觉）。
            if o.lower() == ol or o.lower() in ol:
                return o
        return ""

    def _log_clarify(self, state: AgentState, kind: str, question: str, options: list,
                     answer: str, resolved: str) -> None:
        """§10 澄清回合事件：候选集与用户答复写入现有 Trace（复用 tool_args/tool_result 字段，格式不变）。"""
        self.tracer.log(step=0, type="clarify_round", thought=f"向用户澄清{kind}",
                        tool_args={"question": question, "candidates": list(options)},
                        tool_result={"answer": answer, "resolved": resolved},
                        user_input=state.user_input, state_snapshot=state.snapshot())

    def _default_product_hint(self, products: list) -> str:
        """未给项目名时的兜底：zentao.default_product 需在已知项目内才可用（不编造未知项目）。"""
        dp = (self.cfg.zentao.default_product or "").strip()
        if not dp:
            return ""
        for p in products:
            if p.lower() == dp.lower() or dp.lower() in p.lower() or p.lower() in dp.lower():
                return p
        return ""

    def _llm_fallback_enabled(self) -> bool:
        """兜底开关开启 + LLM 可用才启用；任一不满足退回现有反问链路，行为与未实现时一致。"""
        return bool(getattr(self.cfg.agent, "llm_intent_fallback", False)
                    and self.llm is not None and getattr(self.llm, "available", False))

    def _llm_resolve(self, state: AgentState) -> str:
        """LLM 兜底翻译：把原始输入在「程序给的候选集」内映射到确定版本全名。

        分工沿用 V2-1 §5：事实（候选集与排序）程序钉死，模型只做自然语言翻译；
        模型答复不在候选集内一律返回空串交回现有流程（防幻觉，不编造版本）。
        """
        # 无论有无交互入口，都用兜底项目列候选（无 ask 时这是唯一直接命中路径，能救回 clarify 退出）
        product = self._default_product_hint(self._known_products())
        if not product:
            return ""
        cands = self._candidates(product)
        if not cands:
            return ""
        names = [c["name"] for c in cands]
        prompt = (
            f"用户请求：{state.user_input}\n"
            f"可选版本（仅能从中选一个，按创建时间从新到旧排列）：{names}\n"
            "若能从候选中确定用户意图的版本，只输出该候选的完整名称；"
            "不确定则只输出 None。不要创造候选之外的版本。"
        )
        try:
            answer = self.llm.complete(
                prompt, system="你是输入意图解析器，只能从给定候选中选择，禁止编造。")
        except Exception:
            return ""
        a = (answer or "").strip()
        if not a or a.lower() == "none":
            return ""
        for n in names:
            if n.lower() in a.lower() or a.lower() in n.lower():
                return n
        return ""

    @staticmethod
    def _prefilter(cands: list, version_hint: str) -> list:
        """半截版本号（如「V5」）按前缀预筛候选；命中则收窄，否则原样返回（全版本号不参与预筛）。"""
        if version_hint and version_hint.count(".") < 2:
            token = version_hint.lstrip("Vv").strip()
            pref = [c for c in cands if str(c.get("build", "")).lstrip("Vv").startswith(token)]
            if pref:
                return pref
        return cands

    def _resolve_with_clarify(self, state: AgentState) -> AgentState:
        """三态识别 + 候选反问，将确定的版本全名写回 state.version_name；仍歧义则置 clarify。

        项目/版本各至多问 1 次、总≤２回合；答复无法解析时不硬编，交主链路按现有逻辑处理。
        """
        max_rounds = 2
        products = self._known_products()
        intent = classify_input(state.user_input, products)

        if intent.kind == "complete":
            state.version_name = intent.resolved_version
            return state

        # 相对版本词短路（规则层，不花 token）：「最新的/上个版本」直接取排序后候选，
        # 不再反问——修复「原始输入说了最新还是被问」。「最新」口径仍由候选排序钉死（程序定事实）。
        # 仅在项目已识别时短路：项目未知不猜默认项目（否则会静默产出错项目的报告），退回下面先问项目。
        if intent.rel_hint >= 0 and intent.product_hint:
            product = intent.product_hint
            cands = self._candidates(product)
            if cands and intent.rel_hint < len(cands):
                state.version_name = cands[intent.rel_hint]["name"]
                self.tracer.log(step=0, type="llm_reasoning",
                                thought=f"输入命中相对版本词，直接选定：{state.version_name}",
                                user_input=state.user_input, state_snapshot=state.snapshot())
                return state
            # rel_hint 超出候选数（如只一个版本却问「上一个」）：不猜，继续走后续流程

        # LLM 兜底翻译（新开关 llm_intent_fallback，默认关时下面整段不执行）：
        # 规则不命中 → 模型在程序给的候选集内选择；答不在集内一律丢弃（防幻觉）。
        if self._llm_fallback_enabled():
            hit = self._llm_resolve(state)
            if hit:
                state.version_name = hit
                self.tracer.log(step=0, type="llm_reasoning",
                                thought=f"规则未命中，LLM 在候选集内翻译命中：{hit}",
                                user_input=state.user_input, state_snapshot=state.snapshot())
                return state

        # 项目已知时的确定性收敛：列候选 + 半截版本前缀预筛，唯一命中直接采用，
        # 无需交互入口也能定（只有 >1 的真歧义才需要问人），避免可判定的输入被白白 clarify。
        if intent.product_hint:
            uniq = self._prefilter(self._candidates(intent.product_hint), intent.version_hint)
            if len(uniq) == 1:
                state.version_name = uniq[0]["name"]
                self.tracer.log(step=0, type="llm_reasoning",
                                thought=f"项目已知且候选唯一，直接选定：{state.version_name}",
                                user_input=state.user_input, state_snapshot=state.snapshot())
                return state

        if self.ask is None:
            state.status = "clarify"
            state.fail_reason = "输入不完整且未提供交互入口，无法澄清"
            return state

        rounds = 0
        product = intent.product_hint

        # 缺项目名：先问项目（两缺时的第一回合）
        if not product:
            if rounds >= max_rounds:
                state.status = "clarify"
                state.fail_reason = "澄清回合已达上限"
                return state
            base_q = "未识别到项目名，请选择项目："
            q = self._fc_question(base_q, products)
            ans = self.ask(q, products)
            rounds += 1
            state.clarify_count += 1
            # FC 模式优先模型解析（严格校集内），空则回退规则（序号/包含）
            product = self._fc_parse_answer(ans, products) or self._resolve_product(ans, products)
            self._log_clarify(state, "项目", q, products, ans, product)
            if not product:
                state.status = "clarify"
                state.fail_reason = "项目仍无法识别"
                return state

        # 有项目、无完整版本：列候选（半截版本号按前缀预筛，唯一命中已在上面拦截）
        cands = self._prefilter(self._candidates(product), intent.version_hint)
        if len(cands) == 1:
            state.version_name = cands[0]["name"]
            return state
        if len(cands) == 0:
            # 无候选：交主链路按现有「版本不存在」处理（不编造）
            state.version_name = extract_version_name(state.user_input) or product
            return state

        # 候选 >1：反问版本
        if rounds >= max_rounds:
            state.status = "clarify"
            state.fail_reason = "版本仍有歧义，澄清回合已达上限"
            return state
        names = [c["name"] for c in cands]
        base_q = f"{product} 下有多个版本，请选择（可答序号／版本名／最新的）："
        q = self._fc_question(base_q, names)
        ans = self.ask(q, names)
        rounds += 1
        state.clarify_count += 1
        # FC 模式优先模型解析（严格校集内），空则回退规则（序号/最新/包含）
        chosen = self._fc_parse_answer(ans, names) or self._resolve_version(ans, cands)
        self._log_clarify(state, "版本", q, names, ans, chosen)
        state.version_name = chosen or (extract_version_name(state.user_input) or product)
        return state

    # ---- V2-3 项目级参考文档链接记忆 ----
    def _doc_store(self):
        """惰性创建本地小抄实例：仅 doc_links_enabled 开启时才会被调用，关闭时不触碰文件。"""
        if self._doc_store_obj is None:
            from ..memory.doc_links import DocLinkStore
            self._doc_store_obj = DocLinkStore(self.cfg.doc_links_path)
        return self._doc_store_obj

    def _resolve_project_doc_links(self, product_name: str, state: Optional[AgentState] = None) -> Dict[str, Dict[str, str]]:
        """§1.3 需求/手册/UI/原型 这类「同项目每版本都一样」的链接：
        命中项目记忆直接复用；未命中且有交互入口则首次询问用户提供并登记（严格校验 URL）。

        开关 doc_links_enabled 关闭 / 无产品名一律返回 {}，渲染层保持占位，行为与现状逐字一致。
        严格按项目隔离：只认传入的规范项目名（取自 version_info），新项目返回空→首次询问，绝不借用他项目。
        """
        if not getattr(self.cfg.memory, "doc_links_enabled", False) or not product_name:
            return {}
        from ..memory.doc_links import PROJECT_DOC_TYPES
        store = self._doc_store()
        out: Dict[str, Dict[str, str]] = {}
        for dtype in PROJECT_DOC_TYPES:
            hit = store.lookup(product_name, dtype)
            if hit:
                out[dtype] = hit
                if state is not None:
                    self.tracer.log(step=0, type="memory_reuse",
                                    thought=f"参考文档《{dtype}》复用项目记忆（{product_name}）：{hit['url']}",
                                    tool_result=dict(hit),
                                    user_input=state.user_input, state_snapshot=state.snapshot())
                continue
            if self.ask is None:
                continue  # 无交互入口：不猜不编，保持占位
            q = f"请提供《{dtype}》文档链接（{product_name}，直接回车跳过）："
            ans = (self.ask(q, []) or "").strip()
            if not ans:
                continue
            if store.register(product_name, dtype, ans):
                saved = store.lookup(product_name, dtype)
                if saved:
                    out[dtype] = saved
                    if state is not None:
                        self._log_clarify(state, f"参考文档《{dtype}》", q, [], ans, saved["url"])
            elif state is not None:
                # 非法 URL：不入库，下一轮仍会询问或保持占位（绝不把乱码当链接记住）
                self.tracer.log(step=0, type="clarify_round",
                                thought=f"参考文档《{dtype}》链接格式非法，未记入记忆",
                                tool_args={"question": q}, tool_result={"answer": ans, "resolved": ""},
                                user_input=state.user_input, state_snapshot=state.snapshot())
        return out

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
            # V2-3：以报告权威产品名（version_info）为键查/登项目记忆（关闭时返回 {}，行为不变）
            kwargs["project_doc_links"] = self._resolve_project_doc_links(vi.get("product_name", ""), state)
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
