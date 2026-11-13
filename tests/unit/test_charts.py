"""本地图表生成（Phase 1）单元测试：同源出图、异常降级、渲染层图片行/占位回退。"""
from pathlib import Path

from report_agent.config import load_config
from report_agent.report.charts import render_charts, _pie_pct, _pie_labels
from report_agent.report.renderer import render_report
from report_agent.report import templates as T
from report_agent.tools.render_report import RenderReportTool
from report_agent.tools.statistics import compute_statistics


def _bug(module, severity, status, resolution="fixed", activated=0, opened_by="tester_a", resolved_by="dev_wang", opened_build="V5.0.0"):
    return {"module": module, "severity": severity, "status": status,
            "resolution": resolution, "activatedCount": activated,
            "openedBy": opened_by, "resolvedBy": resolved_by, "openedDate": "2026-01-05T02:00:00Z",
            # 影响版本逐字等于本版本，否则被 collect_people 的精确匹配过滤
            "openedBuild": opened_build}


def _stats():
    bugs = [
        _bug("/登录", 1, "closed"), _bug("/登录", 3, "closed"), _bug("/登录", 3, "active", activated=1),
        _bug("/报表", 2, "resolved"), _bug("/报表", 3, "closed"), _bug("/报表", 4, "active", resolution="postponed"),
        _bug("/同步", 2, "closed"), _bug("/同步", 3, "resolved", activated=2),
    ]
    return compute_statistics({"name": "UniResearch V5.0.0", "build": "V5.0.0", "fetch_status": "ok"}, bugs)


def test_render_charts_produces_five_pngs(tmp_path):
    out = render_charts(_stats(), tmp_path / "assets")
    assert set(out) == {"status_bar", "activated_bar",
                        "person_submit_bar", "person_resolve_bar", "level_pie"}
    for p in out.values():
        assert Path(p).exists() and Path(p).stat().st_size > 1000  # 真实 PNG 非空壳


def test_render_charts_zero_bugs_degrades_to_empty(tmp_path):
    stats = compute_statistics({"name": "空版本", "build": "V0", "fetch_status": "ok"}, [])
    assert stats.summary.total == 0
    assert render_charts(stats, tmp_path / "assets") == {}


def test_level_pie_hides_small_slice_pct():
    """小切片百分比防遮挡：低于门槛不画内部百分比，达标照常显示。"""
    assert _pie_pct(0.8) == "" and _pie_pct(4.9) == ""
    assert _pie_pct(5.0) == "5.0%" and _pie_pct(18.0) == "18.0%"


def test_pie_labels_merge_small_pct_outside():
    """小切片百分比合并进外侧标签（信息不丢）；达标切片标签只列件数，百分比在切片内部。"""
    labels = _pie_labels(["P1", "P2", "P3", "P4"], [1, 22, 98, 1])
    assert labels == ["P1（1 个, 0.8%）", "P2（22 个）", "P3（98 个）", "P4（1 个, 0.8%）"]
    assert _pie_labels(["P1"], [0]) == ["P1（0 个, 0.0%）"]  # 零总数不除零


def test_person_counts_sorted_desc():
    """统计层：每人提交/解决件数按 openedBy/resolvedBy 实算，件数降序。"""
    bugs = [
        _bug("/登录", 3, "closed", opened_by="甲", resolved_by="乙", opened_build="V1.0.0"),
        _bug("/登录", 3, "closed", opened_by="甲", resolved_by="丙", opened_build="V1.0.0"),
        _bug("/报表", 2, "active", opened_by="乙", resolved_by="", opened_build="V1.0.0"),
        _bug("/报表", 3, "closed", opened_by="乙", resolved_by="乙", opened_build="V1.0.0"),
        _bug("/同步", 4, "resolved", opened_by="丙", resolved_by="乙", opened_build="V1.0.0"),
    ]
    st = compute_statistics({"name": "V1.0.0", "build": "V1.0.0", "fetch_status": "ok"}, bugs)
    assert [(p.name, p.total) for p in st.people.submit_counts] == [("甲", 2), ("乙", 2), ("丙", 1)]
    assert [(p.name, p.total) for p in st.people.resolve_counts] == [("乙", 3), ("丙", 1)]  # 未解决无解决人，不计
    md = render_report(st)
    # 4.3/4.4 去表留图：不再输出重复件数表（charts=None 时回退图占位），文字说明行已删除，仅保留图表位
    assert "| 提交人 | Bug 数 |" not in md and "| 解决人 | Bug 数 |" not in md
    assert "### 4.3 每人提交 Bug 数统计" in md and "### 4.4 每人解决 Bug 数统计" in md
    assert "件数标注于柱末" not in md
    assert "每人提交 Bug 数柱状图" in md and "每人解决 Bug 数柱状图" in md


def test_renderer_image_line_and_placeholder_fallback():
    stats = _stats()
    # 无 charts：数据图位回退占位（4.3/4.4 有实算表，图位无 key 也回退占位）
    md = render_report(stats)
    assert T.CHART_PLACEHOLDER.format(item="缺陷状态分布图") in md
    assert "![](" not in md
    # 有 charts：对应位替换为图片行（相对路径、无 alt），缺 key 的仍回退占位；4.1.1 彻底无图无占位
    md2 = render_report(stats, charts={"status_bar": "assets/UniResearch_V5.0.0/status_bar.png"})
    assert "![](assets/UniResearch_V5.0.0/status_bar.png)" in md2
    assert "各模块缺陷分布图" not in md2
    assert T.CHART_PLACEHOLDER.format(item="每人提交 Bug 数柱状图") in md2
    assert T.CHART_PLACEHOLDER.format(item="每人解决 Bug 数柱状图") in md2


def test_tool_with_cfg_embeds_images(tmp_path):
    cfg = load_config(**{"report.output_dir": str(tmp_path), "charts.enabled": True})
    res = RenderReportTool(cfg).run(stats_obj=_stats())
    assert res.ok
    for key in ("status_bar", "activated_bar", "level_pie",
                "person_submit_bar", "person_resolve_bar"):
        assert f"![](assets/UniResearch_V5.0.0/{key}.png)" in res.data
        assert (tmp_path / "assets" / "UniResearch_V5.0.0" / f"{key}.png").exists()


def test_tool_without_cfg_or_disabled_keeps_placeholders(tmp_path):
    stats = _stats()
    assert T.CHART_PLACEHOLDER.format(item="缺陷状态分布图") in RenderReportTool().run(stats_obj=stats).data
    cfg = load_config(**{"report.output_dir": str(tmp_path), "charts.enabled": False})
    md = RenderReportTool(cfg).run(stats_obj=stats).data
    assert "![](" not in md
    assert not (tmp_path / "assets").exists()


def test_renderer_no_module_chart_line():
    """4.1.1 按用户要求彻底去图：模块表后既无图片行也无图表占位。"""
    md = render_report(_stats(), charts={"status_bar": "assets/x/status_bar.png"})
    assert "module_bar" not in md and "各模块缺陷分布" not in md
