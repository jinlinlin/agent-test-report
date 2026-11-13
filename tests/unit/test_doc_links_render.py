"""V2-3 第 2 步：项目记忆接入渲染与循环的单测。

覆盖三层：
- templates.reference_doc_table：任意行带 url 即渲染为链接，未提供行仍占位（旧行为不变）；
- RenderReportTool：project_doc_links 合并进 ref_links，飞书实检在同键时优先于记忆；
- ReportAgent._resolve_project_doc_links：开关关零触碰、命中复用、项目隔离、首次询问登记、非法不入库。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.agent.loop import ReportAgent
from report_agent.memory.doc_links import DocLinkStore
from report_agent.report import templates as T
from report_agent.schemas.contracts import StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool


class _NullTracer:
    def log(self, *args, **kwargs):
        pass


def _mem_cfg(tmp_path, enabled=True):
    return SimpleNamespace(
        memory=SimpleNamespace(doc_links_enabled=enabled),
        doc_links_path=tmp_path / "doc_links.json",
    )


def _agent(cfg, ask=None):
    return ReportAgent(cfg, registry=None, planner=None, tracer=_NullTracer(), ask=ask, llm=None)


# ---- templates ----

def test_reference_table_renders_memory_link_and_keeps_placeholder():
    md = T.reference_doc_table({"产品原型文档": {"title": "原型V5", "url": "https://x/wiki/P"}})
    assert "[原型V5](https://x/wiki/P)" in md
    assert "| 需求文档 | 【待补充】 |" in md  # 未提供的行仍人工占位


# ---- RenderReportTool 合并 ----

def test_tool_merges_project_doc_links_into_report():
    tool = RenderReportTool(None)  # cfg=None → 跳过飞书检索，只走注入的项目记忆
    res = tool.run(stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0",
                   project_doc_links={"需求文档": {"title": "需求X", "url": "https://x/wiki/REQ"}})
    assert res.ok
    assert "[需求X](https://x/wiki/REQ)" in res.data
    assert res.meta["ref_doc_links"]["需求文档"]["url"] == "https://x/wiki/REQ"


def test_tool_backward_compatible_without_project_links():
    res = RenderReportTool(None).run(stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0")
    assert res.ok and res.meta["ref_doc_links"] == {}
    assert "| 需求文档 | 【待补充】 |" in res.data  # 与现状逐字一致


def test_tool_feishu_fact_overrides_memory_on_same_key(monkeypatch):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {
        "测试大纲": {"title": "飞书实检大纲", "url": "https://x/wiki/REAL"},
    })
    feishu = SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    cfg = SimpleNamespace(feishu=feishu, charts=SimpleNamespace(enabled=False, dpi=100), report_dir=".")
    res = RenderReportTool(cfg).run(
        stats_obj=StatsResult(), product_name="UniResearch", build="V5.0.0",
        project_doc_links={"测试大纲": {"title": "记忆旧大纲", "url": "https://x/wiki/STALE"}})
    assert "[飞书实检大纲](https://x/wiki/REAL)" in res.data      # 事实优先
    assert "记忆旧大纲" not in res.data


# ---- loop._resolve_project_doc_links ----

def test_disabled_touches_no_file(tmp_path):
    ag = _agent(_mem_cfg(tmp_path, enabled=False), ask=lambda q, o: "https://x/whatever")
    assert ag._resolve_project_doc_links("UniResearch") == {}
    assert not (tmp_path / "doc_links.json").exists()            # 关闭时绝不创建/读写小抄
    assert ag._doc_store_obj is None                             # 连实例都不建


def test_empty_product_returns_empty(tmp_path):
    ag = _agent(_mem_cfg(tmp_path))
    assert ag._resolve_project_doc_links("") == {}


def test_reuses_registered_link_for_same_project(tmp_path):
    DocLinkStore(tmp_path / "doc_links.json").register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/P")
    ag = _agent(_mem_cfg(tmp_path))  # 无 ask：未登记类型不猜
    out = ag._resolve_project_doc_links("UniResearch")
    assert out["产品原型文档"]["url"] == "https://feishu.cn/wiki/P"
    assert "需求文档" not in out


def test_no_cross_project_reuse(tmp_path):
    DocLinkStore(tmp_path / "doc_links.json").register("UniResearch", "需求文档", "https://feishu.cn/wiki/U")
    ag = _agent(_mem_cfg(tmp_path))
    assert ag._resolve_project_doc_links("一表通项目") == {}      # 新项目绝不借用他项目


def test_first_time_ask_registers_and_persists(tmp_path):
    answers = {"需求文档": "https://feishu.cn/wiki/REQ", "产品原型文档": "https://feishu.cn/wiki/PROTO"}

    def ask(q, opts):
        for k, v in answers.items():
            if k in q:
                return v
        return ""  # 其余类型跳过

    ag = _agent(_mem_cfg(tmp_path), ask=ask)
    out = ag._resolve_project_doc_links("UniResearch")
    assert out["需求文档"]["url"] == "https://feishu.cn/wiki/REQ"
    assert out["产品原型文档"]["url"] == "https://feishu.cn/wiki/PROTO"
    assert "软件用户手册" not in out                             # 空答复不入库
    # 落盘可复用：新实例不再询问也能命中
    assert DocLinkStore(tmp_path / "doc_links.json").lookup("UniResearch", "需求文档")["url"] == "https://feishu.cn/wiki/REQ"


def test_invalid_answer_not_registered(tmp_path):
    ag = _agent(_mem_cfg(tmp_path), ask=lambda q, o: "不是链接")
    out = ag._resolve_project_doc_links("UniResearch")
    assert out == {}
    assert not (tmp_path / "doc_links.json").exists()            # 全非法不产生文件
