"""§1.3 参考文档「测试大纲/测试用例」链接自动检索的单测。

覆盖三层：
- templates.reference_doc_table：链接填充 / 未命中占位 / 新增「测试用例」行；
- feishu_client.find_doc_urls：严格按「产品+版本」作用域命中，缺失一律返回空（不跨版本）；
- render_report 工具：飞书未启用/cfg 缺失/异常均降级为占位，启用时把链接渲染进表。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.report import templates as T
from report_agent.report.renderer import render_report
from report_agent.schemas.contracts import StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool


# ---- templates：表格渲染 ----

def test_reference_table_fills_links_and_has_testcase_row():
    md = T.reference_doc_table({
        "测试大纲": {"title": "V5.0.0 测试大纲", "url": "https://x/wiki/A"},
        "测试用例": {"title": "V5.0.0 测试用例.xlsx", "url": "https://x/wiki/B"},
    })
    assert "| 测试用例 |" in md  # 新增行
    # 链接渲染为「文档名」超链接，而非裸 URL
    assert "[V5.0.0 测试大纲](https://x/wiki/A)" in md
    assert "[V5.0.0 测试用例.xlsx](https://x/wiki/B)" in md
    assert "| 需求文档 | 【待补充】 |" in md  # 其余仍人工


def test_reference_table_accepts_plain_url_string():
    # 兼容直接给 url 字符串：链接文字回退为行名
    md = T.reference_doc_table({"测试大纲": "https://x/wiki/A"})
    assert "[测试大纲](https://x/wiki/A)" in md


def test_reference_table_missing_placeholder():
    md = T.reference_doc_table({})
    assert "未在对应版本目录找到《测试大纲》" in md
    assert "未在对应版本目录找到《测试用例》" in md


# ---- feishu_client：作用域与跨版本防护（桩替换导航原语，不联网）----

def _client():
    f = SimpleNamespace(wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x.feishu.cn")
    return FeishuClient(f)


def _stub(client, project="PRODUCT_NODE", version="V_NODE", nodes=None):
    client.find_child_by_title = lambda space, parent, name: project if name == "UniResearch" else ""
    client.find_child_node = lambda space, parent, build: version if (parent == "PRODUCT_NODE" and build == "V5.0.0") else ""
    client.list_nodes = lambda space, parent: nodes if parent == "V_NODE" else []


def test_find_doc_urls_scoped_match():
    c = _client()
    _stub(c, nodes=[
        {"title": "测试大纲", "node_token": "OUT"},
        {"title": "UniResearch V5.0.0 测试用例", "node_token": "CASE"},
        {"title": "会议纪要", "node_token": "X"},
    ])
    urls = c.find_doc_urls("UniResearch", "V5.0.0", list(T.REF_DOC_LINK_TITLES))
    assert urls == {
        "测试大纲": {"title": "测试大纲", "url": "https://x.feishu.cn/wiki/OUT"},
        "测试用例": {"title": "UniResearch V5.0.0 测试用例", "url": "https://x.feishu.cn/wiki/CASE"},
    }


def test_find_doc_urls_no_cross_version_when_version_dir_absent():
    c = _client()
    _stub(c, version="")  # 版本目录不存在
    c.list_nodes = lambda space, parent: [{"title": "测试大纲", "node_token": "LEAK"}]
    assert c.find_doc_urls("UniResearch", "V9.9.9", list(T.REF_DOC_LINK_TITLES)) == {}


def test_find_doc_urls_no_cross_project_when_project_absent():
    c = _client()
    _stub(c, project="")  # 产品节点匹配不到
    assert c.find_doc_urls("OtherProduct", "V5.0.0", list(T.REF_DOC_LINK_TITLES)) == {}


def test_find_doc_urls_requires_both_product_and_build():
    c = _client()
    assert c.find_doc_urls("", "V5.0.0", ["测试大纲"]) == {}
    assert c.find_doc_urls("UniResearch", "", ["测试大纲"]) == {}


# ---- render_report 工具：降级与启用 ----

def _cfg(enabled: bool):
    feishu = SimpleNamespace(enabled=enabled, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(feishu=feishu, charts=SimpleNamespace(enabled=False, dpi=100), report_dir=".")


def test_tool_disabled_yields_placeholder():
    tool = RenderReportTool(_cfg(enabled=False))
    res = tool.run(stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0")
    assert res.ok and res.meta["ref_doc_links"] == {}
    assert "未在对应版本目录找到《测试大纲》" in res.data


def test_tool_none_cfg_backward_compatible():
    res = RenderReportTool(None).run(stats_obj=StatsResult())
    assert res.ok and "未在对应版本目录找到《测试大纲》" in res.data


def test_tool_enabled_fills_links(monkeypatch):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {
        "测试大纲": {"title": "大纲标题", "url": "https://x/wiki/OUT"},
        "测试用例": {"title": "用例标题", "url": "https://x/wiki/CASE"},
    })
    res = RenderReportTool(_cfg(enabled=True)).run(stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0")
    assert "[大纲标题](https://x/wiki/OUT)" in res.data and "[用例标题](https://x/wiki/CASE)" in res.data


def test_tool_enabled_swallows_exception(monkeypatch):
    def boom(self, p, b, t):
        raise RuntimeError("网络不可达")
    monkeypatch.setattr(FeishuClient, "find_doc_urls", boom)
    res = RenderReportTool(_cfg(enabled=True)).run(stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0")
    assert res.ok and res.meta["ref_doc_links"] == {}  # 异常不阻断渲染


# ---- renderer 端到端签名兼容 ----

def test_renderer_accepts_ref_links_and_legacy_call():
    stats = StatsResult()
    with_links = render_report(stats, ref_links={"测试大纲": {"title": "大纲X", "url": "https://x/wiki/OUT"}})
    legacy = render_report(stats)  # 旧位置调用仍可用
    assert "[大纲X](https://x/wiki/OUT)" in with_links and "https://x/wiki/OUT" not in legacy
