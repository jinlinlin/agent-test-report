"""V2-6 缺陷关闭构成（4.1.2）单测（全离线，不联网）。

覆盖：
- 统计层：_classify_closure 严格窗口归属边界；协助关闭计数（closed_other_bugs 只计窗口内，
  不进总数/覆盖率）；窗口缺失时协助关闭不计；四桶与准入结论不受影响；
- mock 全链路：V5.0.0 已关闭 15 ＋ 协助关闭 V6.0.0 窗口内 3 条；
- 取数层：_normalize 带入 closedDate（详情优先、列表回退）；mock slim 列表同形；
- 渲染层：开关关时报告与现状逐字一致（无 4.1.2 字样）；开关开时呈现版本归属三行表；
  协助关闭为 0 时整节不显示（无构成可拆）；
- RenderReportTool 接线：cfg 缺失/开关关/开关开三态与 meta 回报。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.report.renderer import render_report
from report_agent.schemas.contracts import StatsResult, Summary, VersionInfo
from report_agent.tools import mock_zentao
from report_agent.tools.statistics import _classify_closure, compute_statistics
from report_agent.tools.zentao_bugs import GetVersionBugsTool


def _vi(**over) -> dict:
    vi = {"name": "UniResearch V9.9.9", "build": "V9.9.9", "fetch_status": "ok",
          "test_start": "2026-10-01", "test_end": "2026-10-10"}
    vi.update(over)
    return vi


def _closed(bid, closed_date) -> dict:
    return {"id": bid, "status": "closed", "resolution": "fixed", "severity": 3,
            "module": "/m", "closedDate": closed_date}


# ---- 统计层：严格窗口归属与协助关闭计数 ----

def test_classify_closure_no_guess_on_absent_date():
    assert _classify_closure(_closed(1, "2026-10-05"), (None, None)) == "unknown"
    from datetime import date
    w = (date(2026, 10, 1), date(2026, 10, 10))
    assert _classify_closure(_closed(1, "2026-10-05"), w) == "this_cycle"
    assert _classify_closure(_closed(1, "2026-09-30"), w) == "history"
    # 严格窗口右端：窗口结束次日即归历史
    assert _classify_closure(_closed(1, "2026-10-11"), w) == "history"


def test_closed_other_versions_counted_only_in_window():
    # 协助关闭：同产品其他版本已关闭缺陷，只计窗口内；无日期/窗口外不计
    others = [
        {"id": 101, "status": "closed", "closedDate": "2026-10-03", "openedBuild": "V5.0.0"},
        {"id": 102, "status": "closed", "closedDate": "2026-09-12", "openedBuild": "V4.0.0"},
        {"id": 103, "status": "closed", "closedDate": "", "openedBuild": "V4.0.0"},
    ]
    bugs = [_closed(1, "2026-10-05")]
    s = compute_statistics(_vi(), bugs, others).summary
    assert s.closed_other_versions == 1
    # 红线：不影响本版本总数/已关闭/覆盖率（1 进 1 出仍是 100%）
    assert s.total == 1 and s.closed == 1 and s.fix_coverage == "100.00%"
    # 不传/传空 → 行为与旧一致（默认 0）
    assert compute_statistics(_vi(), bugs).summary.closed_other_versions == 0
    # 测试窗口缺失 → 无从判定归属，协助关闭不计
    s2 = compute_statistics(_vi(test_start="", test_end=""), bugs, others)
    assert s2.summary.closed_other_versions == 0 and s2.summary.closed == 1


# ---- mock 全链路（已关闭 15 ＋ 协助关闭 3）----

def test_mock_v5_full_chain_split():
    vi = mock_zentao.fetch_version("UniResearch V5.0.0").data
    res = mock_zentao.fetch_bugs("UniResearch V5.0.0")
    st = compute_statistics(vi, res.data, res.meta["closed_other_bugs"])
    assert st.summary.closed == 15
    # V6.0.0 窗口内关闭演示 3 条（id 1/2/3）落在 V5.0.0 测试窗口内 → 协助关闭
    assert st.summary.closed_other_versions == 3
    # slim 列表只含其他版本（openedBuild 均非本版本），与 live 取数层同形四字段
    slim = res.meta["closed_other_bugs"]
    assert all(set(b) == {"id", "status", "closedDate", "openedBuild"} for b in slim)
    assert all("V5.0.0" not in str(b["openedBuild"]) for b in slim)


# ---- 取数层：closedDate 规范化 ----

def test_normalize_carries_closed_date():
    from report_agent.config import load_config
    tool = GetVersionBugsTool(load_config())
    raw = {"id": 1, "status": "closed", "closedDate": "2026-10-09T10:00:00Z"}
    assert tool._normalize(raw)["closedDate"] == "2026-10-09T10:00:00Z"
    # 列表缺失时从单条详情补拉；两处皆无 → 空串
    assert tool._normalize(raw, {"closedDate": "2026-10-11T02:00:00Z"})["closedDate"] == "2026-10-11T02:00:00Z"
    assert tool._normalize({"id": 2, "status": "closed"})["closedDate"] == ""


# ---- 渲染层与开关 ----

def _stats() -> StatsResult:
    return StatsResult(
        version=VersionInfo(name="UniResearch V5.0.0", build="V5.0.0",
                            test_start="2026-09-30", test_end="2026-10-23"),
        summary=Summary(total=14, closed=14, fix_coverage="100.00%",
                        closed_other_versions=3),
    )


def test_render_disabled_section_absent():
    st = _stats()
    md = render_report(st)
    assert "4.1.2" not in md and "缺陷关闭构成" not in md
    assert render_report(st, closure_breakdown=False) == md  # 显式关=默认逐字一致


def test_render_enabled_table_and_anchor():
    md = render_report(_stats(), closure_breakdown=True)
    assert "### 4.1.2 缺陷关闭构成" in md
    # 版本归属主轴：用版本 token 而非全名
    assert "| V5.0.0 关闭（= 4.1 已关闭数） | 14 |" in md
    assert "影响版本含 V5.0.0 且已关闭" in md
    assert "关闭时刻落在 V5.0.0 测试窗口内且不属于 V5.0.0 的 bug" in md
    assert "| 本周期协助关闭的其他版本缺陷 | 3 |" in md
    assert "**总关闭（V5.0.0 关闭＋协助关闭）** | **17**" in md
    assert "V5.0.0 关闭＋周期协助关闭的其他版本缺陷" in md
    assert "2026-09-30 ~ 2026-10-23" in md
    # 口径注释行已按用户要求删除
    assert "仍按 4.1 快照口径" not in md
    # 不再显示窗口细分文字
    assert "其中测试窗口内关闭" not in md
    # 4.1 主句与 4.2 之后章节不受影响
    assert "累计发现缺陷 **14** 个" in md and "## 4.2 Bug 激活次数统计" in md


def test_render_zero_assist_close_hides_section():
    # 协助关闭为 0 → 整节 4.1.2 不显示（即使开关开）
    st = _stats()
    st.summary.closed_other_versions = 0
    md = render_report(st, closure_breakdown=True)
    assert "4.1.2" not in md and "缺陷关闭构成" not in md
    assert "## 4.2 Bug 激活次数统计" in md  # 前后章节衔接不受影响


def _cfg(closure=False, selfcheck=False):
    return SimpleNamespace(
        feishu=SimpleNamespace(enabled=False, defect_bitable=False, defect_bitable_embed=False),
        charts=SimpleNamespace(enabled=False, dpi=100),
        report=SimpleNamespace(numeric_selfcheck=selfcheck, closure_breakdown=closure),
        report_dir=".",
    )


def test_tool_wiring_gate():
    from report_agent.tools.render_report import RenderReportTool
    st = _stats()
    off = RenderReportTool(_cfg(closure=False)).run(stats_obj=st, product_name="X", build="V1")
    assert off.ok and "4.1.2" not in off.data and off.meta["closure_breakdown"] is False
    on = RenderReportTool(_cfg(closure=True)).run(stats_obj=st, product_name="X", build="V1")
    assert on.meta["closure_breakdown"] is True and "### 4.1.2 缺陷关闭构成" in on.data
    none = RenderReportTool(None).run(stats_obj=st, product_name="X", build="V1")
    assert "4.1.2" not in none.data  # 旧调用兼容：无 cfg 不渲染构成节
