"""§6 禅道缺陷列表链接 + 本地 md 标题行的单元测试（不联网）。

覆盖：
- templates.defect_list_note / defect_bitable_inline_note：无 url 时与旧文案逐字一致（回退保护），
  有 url 时「禅道」渲染为链接；
- renderer.render_report(zentao_bugs_url=...)：两种第 6 节形态各自带上链接；
- RenderReportTool._bugs_browse_url：base_url/product_id 缺一即空串，命中则拼经典路由；
- main._with_local_title：仅本地落盘补 H1，且幂等不重复加。
"""
from report_agent.config import load_config
from report_agent.main import _with_local_title
from report_agent.report import templates as T
from report_agent.report.renderer import render_report
from report_agent.tools.render_report import RenderReportTool
from report_agent.tools.statistics import compute_statistics

URL = "http://zentao.test/zentao/bug-browse-17-0-all.html"
OLD_NOTE = ("说明：下列清单内嵌本版本需重点关注的两类缺陷（延期处理、激活待解决），字段与禅道一致；"
            "完整缺陷明细仍可在禅道按版本筛选查阅。")
OLD_INLINE = ("本次测试共汇总遗留未解决 1 条、延期处理 0 条，缺陷详细见bug清单。"
              "完整缺陷明细仍可在禅道按版本筛选查阅。")


def _stats(bugs):
    return compute_statistics({"name": "UniResearch V5.1.0", "build": "V5.1.0", "fetch_status": "ok"}, bugs)


def test_defect_list_note_fallback_verbatim():
    assert T.defect_list_note() == OLD_NOTE
    assert T.DEFECT_LIST_NOTE == OLD_NOTE  # 常量仍可用且为无链接形态


def test_defect_list_note_with_url():
    note = T.defect_list_note(URL)
    assert f"[禅道]({URL})" in note
    assert "仍可在禅道按版本筛选查阅。" not in note  # 纯文本尾句被链接版替换


def test_bitable_inline_note_fallback_and_url():
    assert T.defect_bitable_inline_note(0, 1) == OLD_INLINE
    linked = T.defect_bitable_inline_note(0, 1, URL)
    assert f"[禅道]({URL})" in linked and "缺陷详细见下方内嵌清单" in linked


def test_render_section6_wide_table_with_link():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    md_plain = render_report(stats)                       # 不传 url：与旧产出逐字一致
    assert OLD_NOTE in md_plain
    md_link = render_report(stats, zentao_bugs_url=URL)
    assert f"[禅道]({URL})" in md_link


def test_render_section6_inline_with_link():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    md = render_report(stats, defect_bitable_inline=True, zentao_bugs_url=URL)
    assert f"[禅道]({URL})" in md and OLD_INLINE not in md


def test_bugs_browse_url_gating():
    cfg = load_config(**{"zentao.mode": "mock", "zentao.base_url": "http://zentao.test/zentao/"})
    tool = RenderReportTool(cfg)
    assert tool._bugs_browse_url(17) == URL               # 命中：拼经典路由，末尾斜杠不重复
    assert tool._bugs_browse_url(None) == ""              # 缺 product_id 退回纯文本
    assert tool._bugs_browse_url("") == ""
    cfg2 = load_config(**{"zentao.mode": "mock", "zentao.base_url": ""})
    assert RenderReportTool(cfg2)._bugs_browse_url(17) == ""  # 缺 base_url 退回纯文本
    assert RenderReportTool(None)._bugs_browse_url(17) == ""   # 无 cfg 兼容旧调用


def test_with_local_title_idempotent():
    body = "# 1. 引言\n\n正文"
    titled = _with_local_title(body, "UniResearch V5.1.0")
    assert titled.startswith("# UniResearch V5.1.0 测试报告\n\n# 1. 引言")
    assert _with_local_title(titled, "UniResearch V5.1.0") == titled  # 已有标题不重复加
    assert _with_local_title("", "X") == ""
