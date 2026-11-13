# -*- coding: utf-8 -*-
"""V2-1 延伸：模糊输入「听得懂随便怎么问」的单测（规则短路 + LLM 兜底翻译）。

覆盖三条新链路，全部在 agent.interactive_clarify 开启下验证：
1) 相对版本词（最新/上一个）规则层直接命中候选，不反问、不花 token；
2) 半截版本号（V5）按前缀预筛候选，唯一则直接用不追问（补上旧缺口）；
3) llm_intent_fallback 开关：规则未命中时 LLM 仅在程序给的候选集内翻译，
   答复越界一律丢弃退回反问；开关关闭时不产生任何额外 LLM 调用、行为逐字不变。
"""
from report_agent.agent.loop import ReportAgent
from report_agent.agent.planner import build_planner, classify_input
from report_agent.config import load_config
from report_agent.llm.client import LLMClient
from report_agent.main import build_registry
from report_agent.trace.tracer import Tracer

PRODUCTS = ["UniResearch", "一表通项目"]

BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False,
        "charts.enabled": False, "feishu.enabled": False, "report.polish_conclusion": False,
        "agent.interactive_clarify": True}


# ---- 规则层：classify_input 识别相对版本词（纯函数，不触 loop）----
def test_rel_word_latest_maps_to_index_zero():
    i = classify_input("UniResearch 最新的报告", PRODUCTS)
    assert i.kind == "incomplete"
    assert i.rel_hint == 0                # 最新 → 候选 0


def test_rel_word_previous_maps_to_index_one():
    i = classify_input("UniResearch 上一个版本", PRODUCTS)
    assert i.kind == "incomplete"
    assert i.rel_hint == 1                # 上一个 → 候选 1


def test_full_version_wins_over_rel_word():
    # 有全版本号时「最新」只是赘词，仍走完整态、不算 rel
    i = classify_input("UniResearch V5.0.0 最新的那版", PRODUCTS)
    assert i.kind == "complete"
    assert i.rel_hint == -1


def test_abbreviated_project_prefix_matches():
    # 简称「一表通」应补到全名「一表通项目」（修复实演发现的项目漏识别）
    i = classify_input("一表通 最新的报告", PRODUCTS)
    assert i.product_hint == "一表通项目"
    assert i.rel_hint == 0


def test_rel_word_generic_word_not_mistaken_as_project():
    # “最新的/报告”等普通词不应误抢为项目名
    i = classify_input("最新的报告", PRODUCTS)
    assert i.product_hint == ""


# ---- 循环层：相对词直接命中，不反问 ----
class _Ask:
    def __init__(self, answers=None):
        self._answers = list(answers or [])
        self.calls = []

    def __call__(self, question, options):
        self.calls.append((question, list(options)))
        idx = len(self.calls) - 1
        return self._answers[idx] if idx < len(self._answers) else ""


class _FakeLLM:
    """available=True、complete 返回预置文本；记录调用次数以验证开关关闭时不触发。"""
    def __init__(self, answer):
        self.answer = answer
        self.available = True
        self.calls = 0

    def complete(self, prompt, system=None):
        self.calls += 1
        return self.answer


def _build(ask=None, llm=None, **extra):
    cfg = load_config(**{**BASE, **extra})
    registry = build_registry(cfg)
    planner = build_planner(cfg, LLMClient(cfg), registry.specs())
    return ReportAgent(cfg, registry, planner, Tracer(enabled=False), ask=ask, llm=llm)


def test_rel_word_latest_resolves_without_asking():
    ask = _Ask()
    st = _build(ask=ask).run("UniResearch 最新的报告")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V6.1.0"   # 候选 0 = 创建最晚
    assert ask.calls == []                             # 未反问


def test_rel_word_previous_resolves_without_asking():
    ask = _Ask()
    st = _build(ask=ask).run("UniResearch 上一个版本")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V6.0.0"   # 候选 1 = 次新
    assert ask.calls == []


def test_rel_word_unknown_project_does_not_guess_default():
    # 只说「最新的」未给项目：不猜默认项目（避免错报），无交互入口时退回 clarify
    st = _build(ask=None).run("最新的报告")
    assert st.status == "clarify"


# ---- 循环层：半截版本号按前缀预筛，唯一直接命中（旧缺口修复）----
def test_partial_version_prefilter_single_hit():
    ask = _Ask()
    st = _build(ask=ask).run("UniResearch V5 出个报告")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V5.0.0"   # 前缀 5 预筛后唯一
    assert ask.calls == []                             # 不再无谓追问


# ---- LLM 兜底翻译：开关开启、规则未命中时在候选集内选择 ----
def test_llm_fallback_hits_candidate_without_asking():
    ask = _Ask()
    fake = _FakeLLM("UniResearch V6.0.0")
    st = _build(ask=ask, llm=fake, **{"agent.llm_intent_fallback": True}).run("帮我出个测试报告")
    assert st.status == "finished"
    assert st.version_name == "UniResearch V6.0.0"
    assert fake.calls == 1
    assert ask.calls == []                             # LLM 命中即免反问


def test_llm_fallback_rejects_out_of_candidate_answer():
    # 模型编造候选之外的版本 → 丢弃，退回反问链路（ask=None → clarify，不硬编）
    fake = _FakeLLM("UniResearch V9.9.9")
    st = _build(ask=None, llm=fake, **{"agent.llm_intent_fallback": True}).run("帮我出个测试报告")
    assert st.version_name != "UniResearch V9.9.9"
    assert st.status == "clarify"


def test_llm_fallback_none_answer_degrades():
    fake = _FakeLLM("None")                            # 模型明确表示不确定
    st = _build(ask=None, llm=fake, **{"agent.llm_intent_fallback": True}).run("帮我出个测试报告")
    assert st.status == "clarify"


# ---- 开关关闭：不产生任何额外 LLM 调用，行为与延伸前逐字一致 ----
def test_llm_fallback_off_makes_no_llm_call():
    fake = _FakeLLM("UniResearch V6.0.0")
    ask = _Ask(["1", "1"])                              # 关时仍走原两回合反问：选项目 1、版本 1
    st = _build(ask=ask, llm=fake).run("帮我出个测试报告")   # 未传 llm_intent_fallback → 默认关
    assert fake.calls == 0                             # 完全不调 LLM
    assert len(ask.calls) == 2                         # 先问项目再问版本（原两回合行为）
    assert st.status == "finished"
    assert st.version_name == "UniResearch V6.1.0"
