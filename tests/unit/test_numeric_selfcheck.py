"""V2-7 报告数字自检单测（全离线，不联网、不调 LLM）。

覆盖：
- 开关关闭（含 cfg=None）：RenderReportTool 输出与 render_report 逐字一致、meta 不含自检键；
- 开关开启 + 正常渲染：零差异，正文逐字不变，ok=True；
- 4.1/4.2 正文数字被篡改：按统计源值重渲染该句并在顶部插入标注块（不静默）；
- evidence/聚合量数字缺失：仅标注不硬改（无法凭空补回）；
- data_complete=False：整体跳过核对（skipped 标记）；
- 句式不匹配（模板改动）：不误报数字错误，交由模板稳定性测试守门。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.report.numeric_selfcheck import apply, headline_numbers
from report_agent.report.renderer import render_report
from report_agent.schemas.contracts import Conclusion, Integrity, StatsResult, Summary
from report_agent.tools.render_report import RenderReportTool


def _stats(**over) -> StatsResult:
    s = StatsResult(
        summary=Summary(total=12, closed=8, pending_resolve=2, pending_accept=1, postponed=1,
                        fix_coverage="75.00%", activated_ge1_count=3, activated_rate="25.00%"),
        conclusion=Conclusion(release_advice="建议发布",
                              evidence=["P1 激活 0 个，满足准入门槛", "P2 激活占比 8.33%"]),
    )
    for k, v in over.items():
        setattr(s.summary, k, v)
    return s


def _cfg(selfcheck=True) -> SimpleNamespace:
    return SimpleNamespace(
        feishu=SimpleNamespace(enabled=False, defect_bitable=False, defect_bitable_embed=False),
        charts=SimpleNamespace(enabled=False, dpi=100),
        report=SimpleNamespace(numeric_selfcheck=selfcheck),
        report_dir=".",
    )


# ---- 校验器本体 ----

def test_clean_render_zero_diff_untouched():
    st = _stats()
    md = render_report(st)
    out, rep = apply(md, st)
    assert out == md
    assert rep == {"ok": True, "corrected": [], "missing": []}
    assert "数字自检" not in out


def test_tampered_41_restated_from_source_with_annotation():
    st = _stats()
    md = render_report(st).replace("累计发现缺陷 **12** 个", "累计发现缺陷 **99** 个")
    out, rep = apply(md, st)
    assert rep["ok"] is False
    assert rep["corrected"] == [{"field": "缺陷总数", "stated": "99", "expected": "12"}]
    assert "累计发现缺陷 **12** 个" in out  # 已按统计源值重渲染
    assert "99" not in out.splitlines()[0]  # 原错误值不在标注首行
    assert out.startswith("> ⚠️ **数字自检（V2-7）**")


def test_tampered_42_restated_from_source():
    st = _stats()
    md = render_report(st).replace("激活率 **25.00%**。", "激活率 **50.00%**。")
    out, rep = apply(md, st)
    assert rep["corrected"] == [{"field": "激活率", "stated": "50.00%", "expected": "25.00%"}]
    assert "激活率 **25.00%**。" in out
    assert "数字自检（V2-7）" in out


def test_missing_evidence_number_annotated_only():
    st = _stats()
    md = render_report(st).replace("P2 激活占比 8.33%", "P2 激活占比偏高")
    out, rep = apply(md, st)
    assert rep["ok"] is False
    assert rep["corrected"] == []
    assert {"field": "准入依据数字 8.33", "expected": "8.33"} in rep["missing"]
    assert "报告缺失（未能定位、请人工核对）" in out


def test_incomplete_data_skipped():
    st = _stats()
    st.integrity = Integrity(data_complete=False, missing_fields=["bugs"])
    md = render_report(st)
    out, rep = apply(md, st)
    assert out == md
    assert rep["skipped"] == "data_incomplete" and rep["ok"] is True


def test_sentence_pattern_absent_no_false_alarm():
    """模板改动导致锚定句缺失：不报错、不误改（与「数字错误」区分开）。"""
    st = _stats()
    out, rep = apply("（一份没有 4.1/4.2 句式的文档）", st)
    assert rep["corrected"] == []
    assert out.startswith("> ⚠️")  # 聚合量缺失走标注而非静默
    assert {"field": "缺陷总数", "expected": "12"} in rep["missing"]


def test_headline_numbers_cover_summary():
    assert headline_numbers(_stats())["修复覆盖率"] == "75.00%"


# ---- RenderReportTool 接线与开关门控 ----

def test_wiring_disabled_output_identical():
    st = _stats()
    res = RenderReportTool(_cfg(selfcheck=False)).run(stats_obj=st, product_name="X", build="V1")
    assert res.ok and res.data == render_report(st)
    assert "numeric_selfcheck" not in res.meta
    res_none = RenderReportTool(None).run(stats_obj=st, product_name="X", build="V1")
    assert res_none.data == render_report(st) and "numeric_selfcheck" not in res_none.meta


def test_wiring_enabled_records_report_and_fixes():
    st = _stats()
    res = RenderReportTool(_cfg(selfcheck=True)).run(stats_obj=st, product_name="X", build="V1")
    assert res.ok
    # 正常渲染：开启自检也应零差异、正文不变，meta 带回自检报告
    assert res.data == render_report(st)
    assert res.meta["numeric_selfcheck"] == {"ok": True, "corrected": [], "missing": []}
