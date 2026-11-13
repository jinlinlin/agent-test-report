# -*- coding: utf-8 -*-
"""V2-1 FC 话术子项单测：原生 FC 模式下澄清话术由模型生成、答复由模型解析为确定候选。

验证要点（§4）：
- FC 激活（use_native_fc=True 且 LLM 可用）：反问话术取自模型；答复优先模型解析，
  但严格校验在程序候选集内，越界/异常/None 一律回退现有规则（序号/最新/包含）；
- 非 FC（use_native_fc=False）：话术仍用模板、答复仍用规则，且 0 次 LLM 调用（逐字不变）；
- 澄清回合候选集与答复写入 Trace（type=clarify_round），格式沿用现有字段。
构造 ReportAgent 直接调 _resolve_with_clarify（planner 未参与澄清，传 None 即可），不触发完整取数链路。
"""
from report_agent.agent.loop import ReportAgent
from report_agent.agent.state import AgentState
from report_agent.config import load_config
from report_agent.main import build_registry
from report_agent.trace.tracer import Tracer

BASE = {"zentao.mode": "mock", "trace.enabled": False, "charts.enabled": False,
        "feishu.enabled": False, "report.polish_conclusion": False,
        "agent.interactive_clarify": True}


class _Ask:
    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    def __call__(self, question, options):
        self.calls.append((question, list(options)))
        return self._answers[len(self.calls) - 1] if len(self.calls) <= len(self._answers) else ""


class _ScriptLLM:
    """按 system 路由：含「话术生成」→返回 question；含「解析」→按调用顺序返回 parses 列表项
    （耗尽后重复最后一项；可为 Exception 以模拟失败）。单值 parse 语法糖包成列表。"""
    available = True

    def __init__(self, question="请选择你需要的版本：", parse="", parses=None):
        self.question = question
        self.parses = list(parses) if parses is not None else [parse]
        self._i = 0
        self.calls = []

    def _next_parse(self):
        v = self.parses[self._i] if self._i < len(self.parses) else self.parses[-1]
        self._i += 1
        return v

    def complete(self, prompt, system=None):
        self.calls.append((prompt, system))
        if system and "话术生成" in system:
            return self.question
        if system and "解析" in system:
            v = self._next_parse()
            if isinstance(v, Exception):
                raise v
            return v
        return ""


def _build(ask, llm, fc=True, tracer=None):
    cfg = load_config(**{**BASE, "llm.use_native_fc": fc})
    registry = build_registry(cfg)
    return ReportAgent(cfg, registry, planner=None, tracer=tracer or Tracer(enabled=False),
                       ask=ask, llm=llm)


def _resolve(agent, text):
    return agent._resolve_with_clarify(AgentState(user_input=text))


# ---- FC 激活：话术由模型生成 ----
def test_fc_question_comes_from_model():
    ask = _Ask(["最新的"])
    llm = _ScriptLLM(question="在 UniResearch 的这些版本里，你想要哪一版？", parse="UniResearch V6.1.0")
    st = _resolve(_build(ask, llm), "UniResearch 出个报告")
    assert ask.calls, "应发生版本反问"
    assert ask.calls[-1][0] == "在 UniResearch 的这些版本里，你想要哪一版？"  # 话术非模板


# ---- FC 激活：答复由模型解析（规则解析不了的措辞，模型能定）----
def test_fc_model_parses_where_rules_cannot():
    ask = _Ask(["随便挑个新一点的那版"])              # 规则 _resolve_version 解析不出（无序号/最新/包含）
    llm = _ScriptLLM(parse="UniResearch V6.0.0")       # 模型在候选集内选定
    st = _resolve(_build(ask, llm), "UniResearch 出个报告")
    assert st.status == "running"
    assert st.version_name == "UniResearch V6.0.0"


# ---- FC 激活：模型越界答复被丢弃、回退规则 ----
def test_fc_out_of_set_answer_falls_back_to_rules():
    ask = _Ask(["1"])                                  # 规则能按序号解析
    llm = _ScriptLLM(parse="UniResearch V9.9.9")       # 候选之外的编造 → _fc_parse_answer 返回 ''
    st = _resolve(_build(ask, llm), "UniResearch 出个报告")
    assert st.version_name == "UniResearch V6.1.0"     # 回退规则：序号 1 = 最新
    assert st.version_name != "UniResearch V9.9.9"


# ---- FC 激活：模型异常时回退规则 ----
def test_fc_model_exception_falls_back_to_rules():
    ask = _Ask(["最新的"])
    llm = _ScriptLLM(parse=RuntimeError("boom"))       # 解析调用抛错
    st = _resolve(_build(ask, llm), "UniResearch 出个报告")
    assert st.version_name == "UniResearch V6.1.0"     # 回退规则仍命中


# ---- FC 激活：两缺先问项目，项目答复由模型解析；版本回退序号规则 ----
def test_fc_project_answer_parsed_by_model():
    ask = _Ask(["随便你看着办", "1"])                  # 项目答复含糊、版本答序号 1
    # 项目回合模型给 UniResearch；版本回合模型返回 None →回退规则按序号 1 取最新
    llm = _ScriptLLM(parses=["UniResearch", "None"])
    st = _resolve(_build(ask, llm), "出个报告吧")
    assert len(ask.calls) == 2                          # 先问项目再问版本
    assert st.version_name == "UniResearch V6.1.0"     # 项目由模型、版本由规则


# ---- 非 FC：话术用模板、答复用规则、0 次 LLM 调用（逐字不变）----
def test_non_fc_no_llm_call_template_question():
    ask = _Ask(["最新的"])
    llm = _ScriptLLM(question="绝不该被用到", parse="UniResearch V6.0.0")
    st = _resolve(_build(ask, llm, fc=False), "UniResearch 出个报告")
    assert llm.calls == []                              # 关时完全不碰模型
    assert ask.calls[-1][0].startswith("UniResearch 下有多个版本")  # 模板话术
    assert st.version_name == "UniResearch V6.1.0"     # 规则解析「最新的」→最新


# ---- 澄清回合写入 Trace（候选集 + 答复）----
def test_clarify_round_logged_to_trace():
    tr = Tracer(enabled=True)
    ask = _Ask(["最新的"])
    llm = _ScriptLLM(parse="")
    agent = _build(ask, llm, fc=False, tracer=tr)
    _resolve(agent, "UniResearch 出个报告")
    rounds = [r for r in tr.records if r["type"] == "clarify_round"]
    assert rounds and rounds[-1]["tool_args"]["candidates"]
    assert rounds[-1]["tool_result"]["answer"] == "最新的"
