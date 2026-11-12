"""F17 结论段 LLM 润色单元测试（对应需求文档验收 A9）。

覆盖 A9 三判定：润色数字集合与输入一致；人为构造不一致时回退模板原文；
开关关闭时工具不注册、链路行为与现状一致。全程使用 FakeLLM，不打真实 API。
"""
from report_agent.config import load_config
from report_agent.main import build_registry
from report_agent.tools.polish_conclusion import PolishConclusionTool, extract_numbers

ORIGINAL = "依据激活 P1=0、P2 激活占比 6.00%，未满足发布准入标准，建议暂缓发布。"
EVIDENCE = [
    "激活 P1=0（门槛：=0），达标",
    "激活 P2 占比 6.00%（门槛：≤5.00%），未达标",
]


class _Conclusion:
    def __init__(self):
        self.release_advice = ORIGINAL
        self.evidence = list(EVIDENCE)


class _Stats:
    def __init__(self):
        self.conclusion = _Conclusion()


class FakeLLM:
    def __init__(self, reply="", raise_exc=False):
        self.available = True
        self.reply = reply
        self.raise_exc = raise_exc

    def complete(self, prompt, system=""):
        if self.raise_exc:
            raise RuntimeError("模拟 API 异常")
        return self.reply


class UnavailableLLM:
    available = False

    def complete(self, prompt, system=""):  # pragma: no cover - 不应被调用
        raise AssertionError("不可用时不应调用 LLM")


def _tool(reply=None, llm=None):
    cfg = load_config(**{"report.polish_conclusion": True})
    if llm is None:
        llm = FakeLLM(reply or ORIGINAL)
    return PolishConclusionTool(cfg, llm)


def test_extract_numbers():
    assert extract_numbers("占比 6.00%，P1=0") == {"6.00", "1", "0"}
    assert extract_numbers("") == set()


def test_polish_applied_when_numbers_preserved():
    """润色保留全部原数字且未引入新数字 → 替换为润色文本。"""
    polished = "综合判定：激活 P1 为 0，但 P2 激活占比达 6.00%，超出准入要求，建议暂缓本次发布。"
    r = _tool(reply=polished).run(stats_obj=_Stats())
    assert r.ok and r.meta["polished"] is True
    assert r.data == polished


def test_fallback_on_new_number():
    """人为构造引入新数字的润色文本 → 拒绝替换，回退模板原文。"""
    polished = ORIGINAL + " 残余风险约 0.5%。"
    s = _Stats()
    r = _tool(reply=polished).run(stats_obj=s)
    assert r.ok and r.meta == {"polished": False, "reason": "numbers_mismatch"}
    assert s.conclusion.release_advice == ORIGINAL


def test_fallback_on_dropped_number():
    """润色丢失原建议关键数字 → 回退模板原文。"""
    s = _Stats()
    r = _tool(reply="版本质量总体可控，建议按计划发布。").run(stats_obj=s)
    assert r.ok and r.meta["polished"] is False and r.data == ORIGINAL
    assert s.conclusion.release_advice == ORIGINAL


def test_fallback_on_llm_error():
    """LLM 调用异常 → 非致命，回退模板原文。"""
    r = _tool(llm=FakeLLM(raise_exc=True)).run(stats_obj=_Stats())
    assert r.ok and r.data == ORIGINAL
    assert r.meta["polished"] is False and r.meta["reason"].startswith("llm_error")


def test_llm_unavailable_keeps_template():
    """无 Key 环境（llm 不可用）→ 不调用模型、原文返回。"""
    r = _tool(llm=UnavailableLLM()).run(stats_obj=_Stats())
    assert r.ok and r.data == ORIGINAL
    assert r.meta["polished"] is False and r.meta["reason"] == "llm_unavailable"


def test_missing_stats_obj():
    r = _tool().run(stats_obj=None)
    assert r.ok is False


def test_registry_gated_by_switch():
    """开关关闭时工具不注册（A9：关闭时行为与现状逐字一致的前提）。"""
    off = load_config()  # 默认 polish_conclusion=false
    assert "polish_conclusion" not in build_registry(off).names()
    on = load_config(**{"report.polish_conclusion": True})
    assert "polish_conclusion" in build_registry(on, FakeLLM()).names()
