"""§1.3 手册/UI/原型链接接地单测（全离线桩，不联网）。

覆盖四层：
- doc_index.parse_doc_link_blocks：实测 blocks 形态（明文 URL / hyperlink 解码 / 形态不符不填）；
- doc_index.resolve_project_doc_index：复用项目层候选检索（跳过版本目录）、单源采纳、异常回退 {}；
- FeishuClient.read_doc_blocks：失败降级空列表不抛断；
- RenderReportTool：开关关闭零触碰网络；doc_index 实检覆盖记忆、不碰大纲/用例/需求键。
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import quote

from report_agent.report.doc_index import (
    DOC_INDEX_ROW_NAMES, parse_doc_link_blocks, resolve_project_doc_index,
)
from report_agent.report import templates as T
from report_agent.tools.feishu_client import FeishuClient, FeishuError
from report_agent.tools.render_report import RenderReportTool


def _text_block(content: str = "", link_url: str = "") -> dict:
    run: dict = {"content": content}
    if link_url:
        run["text_element_style"] = {"link": {"url": link_url}}
    else:
        run["text_element_style"] = {}
    return {"block_id": f"b{id(content)}", "block_type": 2, "text": {"elements": [{"text_run": run}]}}


# 实测形态（Uniresearch原型、UI设计、用户手册链接）：表头 + 三组「名|链接」相邻文本块
_BLOCKS = [
    _text_block("文档"), _text_block("链接"),
    _text_block("软件用户手册"), _text_block("https://docs.uniresearch.ai/"),
    _text_block("UI设计文档"), _text_block("Figma", link_url=quote("https://www.figma.com/design/sxLT?node-id=5585-888", safe="")),
    _text_block("产品原型文档"), _text_block("Uniresearch-原型", link_url=quote("https://modao.cc/proto/i09nbH/sharing", safe="")),
]


# ---- parse_doc_link_blocks ----

def test_parse_real_blocks_shape():
    hits = parse_doc_link_blocks(_BLOCKS)
    assert hits["软件用户手册"] == "https://docs.uniresearch.ai/"
    # hyperlink 显示文字不参与、URL 百分号解码后逐字采纳；行名去空白全等（UI设计文档 → "UI 设计文档"）
    assert hits["UI 设计文档"] == "https://www.figma.com/design/sxLT?node-id=5585-888"
    assert hits["产品原型文档"] == "https://modao.cc/proto/i09nbH/sharing"
    assert set(hits) == set(DOC_INDEX_ROW_NAMES)


def test_parse_requires_adjacent_url():
    # 名称块后没有紧随的可解析链接块 → 不填
    assert parse_doc_link_blocks([_text_block("软件用户手册")]) == {}
    assert parse_doc_link_blocks([_text_block("软件用户手册"), _text_block("见附件")]) == {}
    # link.url 解码后不是 http 开头 → 视为无链接
    bad = [_text_block("产品原型文档"), _text_block("原型", link_url=quote("/relative/path", safe=""))]
    assert parse_doc_link_blocks(bad) == {}
    assert parse_doc_link_blocks([]) == {}


def test_parse_ignores_unrelated_and_non_text_blocks():
    blocks = [_text_block("随便一段话"), {"block_type": 32, "table_cell": {}}] + _BLOCKS
    assert parse_doc_link_blocks(blocks) == parse_doc_link_blocks(_BLOCKS)


# ---- resolve_project_doc_index（项目层候选桩导航，复用 2.2 同一套收集）----

def _stub_client(tree: dict, blocks_by_token: dict, project="P_NODE"):
    f = SimpleNamespace(wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(
        f=f,
        find_child_by_title=lambda space, parent, name: project if name == "UniResearch" else "",
        list_nodes=lambda space, parent: tree.get(parent, []),
        read_doc_blocks=lambda token: blocks_by_token.get(token, []),
    )


_TREE = {
    "P_NODE": [
        {"title": "V5.1.0", "obj_type": "docx", "node_token": "V_NODE", "has_child": True},
        {"title": "UniResearch 相关账号及说明文档", "obj_type": "docx", "node_token": "ACC",
         "obj_token": "ACC_DOC", "has_child": True},
    ],
    # 版本目录不下探：其中的「用户手册」文档绝不能成为候选
    "V_NODE": [{"title": "V5.1.0 用户手册", "obj_type": "docx", "node_token": "INVER", "obj_token": "I_DOC"}],
    "ACC": [{"title": "Uniresearch原型、UI设计、用户手册链接", "obj_type": "docx",
             "node_token": "IDX", "obj_token": "IDX_DOC", "has_child": False}],
}
_KW = ["原型", "手册", "链接"]


def test_resolve_first_hit_single_source():
    blocks = {
        # 父文档「相关账号及文档链接」命中关键词但无可解析行组 → 继续试下一份
        "ACC_DOC": [_text_block("这里只有一些说明")],
        "IDX_DOC": _BLOCKS,
    }
    hits = resolve_project_doc_index(_stub_client(_TREE, blocks), "UniResearch", _KW)
    assert hits["软件用户手册"] == "https://docs.uniresearch.ai/"
    assert len(hits) == 3


def test_resolve_miss_and_exception_return_empty():
    assert resolve_project_doc_index(_stub_client(_TREE, {}), "UniResearch", _KW) == {}
    assert resolve_project_doc_index(_stub_client(_TREE, {"IDX_DOC": _BLOCKS}), "Other", _KW) == {}

    boom = _stub_client(_TREE, {"IDX_DOC": _BLOCKS})
    boom.find_child_by_title = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络异常"))
    assert resolve_project_doc_index(boom, "UniResearch", _KW) == {}


# ---- FeishuClient.read_doc_blocks：降级不抛断 ----

def test_read_doc_blocks_degrades_to_empty(monkeypatch):
    c = FeishuClient(SimpleNamespace(api_base="https://x"))
    assert c.read_doc_blocks("") == []

    def _boom(path, params=None):
        raise FeishuError("404", "not_found")
    monkeypatch.setattr(c, "_get", _boom)
    assert c.read_doc_blocks("DOC") == []


# ---- RenderReportTool 接线：开关守卫与合并优先级 ----

def _stats():
    from report_agent.schemas.contracts import StatsResult
    return StatsResult()


def _cfg(enabled: bool):
    return SimpleNamespace(
        doc_index=SimpleNamespace(enabled=enabled, doc_keywords=("原型", "手册", "链接")),
        feishu=SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                               wiki_space_id="S", wiki_projects_parent="R", doc_base_url="https://x"),
        charts=SimpleNamespace(enabled=False, dpi=100), report_dir=".")


def test_tool_disabled_zero_network(monkeypatch):
    calls = []
    monkeypatch.setattr("report_agent.tools.render_report.FeishuClient",
                        lambda f: calls.append(1))
    assert RenderReportTool(_cfg(False))._resolve_doc_index("UniResearch") == {}
    assert RenderReportTool(None)._resolve_doc_index("UniResearch") == {}
    assert not calls  # 开关关闭：连 FeishuClient 都不实例化


def test_tool_run_index_overrides_memory_not_version_keys(monkeypatch):
    import report_agent.report.doc_index as di
    monkeypatch.setattr(di, "resolve_project_doc_index",
                        lambda client, p, kw: {"软件用户手册": "https://idx/handbook",
                                                "UI 设计文档": "https://idx/ui"})
    # 射掉版本目录实检的真实网络（本用例只验链接汇总链路）
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {"测试大纲": {"title": "测试大纲", "url": "https://v/outline"}})
    res = RenderReportTool(_cfg(True)).run(
        stats_obj=_stats(), product_name="UniResearch", build="V5.1.0",
        project_doc_links={"软件用户手册": {"url": "https://mem/old", "title": "软件用户手册"}})
    links = res.meta["ref_doc_links"]
    assert links["软件用户手册"] == "https://idx/handbook"  # 实检覆盖记忆
    assert links["UI 设计文档"] == "https://idx/ui"
    assert links["测试大纲"]["url"] == "https://v/outline"  # 版本目录键不受影响
    assert res.meta["doc_index_filled"] is True
    # 1.3 表实际渲染：命中行为超链接，未命中的原型行维持人工占位
    md = res.data
    assert "[软件用户手册](https://idx/handbook)" in md
    assert "| 产品原型文档 | 【待补充】 |" in md
    assert "[测试大纲](https://v/outline)" in md


def test_tool_run_disabled_keeps_memory(monkeypatch):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    res = RenderReportTool(_cfg(False)).run(
        stats_obj=_stats(), product_name="UniResearch", build="V5.1.0",
        project_doc_links={"产品原型文档": {"url": "https://mem/proto", "title": "产品原型文档"}})
    assert res.meta["doc_index_filled"] is False
    assert "[产品原型文档](https://mem/proto)" in res.data  # 关闭时记忆链路行为不变
