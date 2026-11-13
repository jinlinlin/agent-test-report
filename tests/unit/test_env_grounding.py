"""2.2 测试环境接地单测（全离线桩，不联网）。

覆盖四层：
- test_env.parse_env_rows：实测正文形态逐字解析；行组不齐/形态不符一律不填（宁缺毋滥）；
- test_env.collect_env_candidates / resolve_test_env：项目层检索（跳过版本目录、深度 ≤2）、
  逐份尝试命中即停、全未命中与异常回退 None；
- FeishuClient.read_doc_raw_content：失败降级空串不抛断；
- renderer/RenderReportTool：test_env 为 None 或开关关闭时 2.2 与现状逐字一致。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.report.renderer import render_report
from report_agent.report.test_env import (
    ENV_ROW_NAMES, collect_env_candidates, parse_env_rows,
    render_env_table, resolve_test_env,
)
from report_agent.report import templates as T
from report_agent.schemas.contracts import StatsResult
from report_agent.tools.feishu_client import FeishuClient, FeishuError
from report_agent.tools.render_report import RenderReportTool

# 实测正文形态（UniResearch 相关账号及说明文档 → Uniresearch测试环境及账号）
_ENV_DOC = """
Uniresearch测试环境及账号

环境

测试地址

账号密码

测试环境

http://192.168.2.136:32012

18510000000/Admin@123

预发布环境

https://pre-uniresearch.uniplore.com

19800000000/Admin@123

生产环境

https://app.uniresearch.cn

19800000000/Admin@123
"""


# ---- parse_env_rows ----

def test_parse_real_doc_shape():
    rows = parse_env_rows(_ENV_DOC)
    assert rows["测试环境"] == {"url": "http://192.168.2.136:32012", "creds": "18510000000/Admin@123"}
    assert rows["生产环境"]["url"] == "https://app.uniresearch.cn"
    assert set(rows) == set(ENV_ROW_NAMES)


def test_parse_requires_full_row_group():
    # URL 缺失（下一行不是 http）→ 该行不填；乱序/散文文本 → 全空
    assert parse_env_rows("测试环境\n随便一句话\n18510000000/Admin@123") == {}
    assert parse_env_rows("这里写的是环境说明，没有行组") == {}
    assert parse_env_rows("") == {}


def test_parse_creds_not_next_env_name():
    # 凭证位落在下一个环境名上（说明本行缺凭证）→ 不填
    assert parse_env_rows("测试环境\nhttps://a.cn\n预发布环境\nhttps://b.cn\nx/y") == {
        "预发布环境": {"url": "https://b.cn", "creds": "x/y"}}


# ---- render_env_table ----

def test_render_table_verbatim_and_placeholder():
    rows = {"测试环境": {"url": "http://x.cn", "creds": "a/b"}}
    md = render_env_table(rows, source_title="某环境文档")
    assert "| 测试环境 | http://x.cn | a/b |" in md
    assert "| 预发布环境 | 【待补充】 | 【待补充】 |" in md
    assert "逐字引自知识库《某环境文档》" in md


# ---- 项目层候选收集（桩导航原语，不联网）----

def _stub_client(tree: dict, project="P_NODE"):
    f = SimpleNamespace(wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    c = SimpleNamespace(
        f=f,
        find_child_by_title=lambda space, parent, name: project if name == "UniResearch" else "",
        list_nodes=lambda space, parent: tree.get(parent, []),
    )
    return c


_TREE = {
    "P_NODE": [
        {"title": "V5.0.0", "obj_type": "docx", "node_token": "V_NODE", "has_child": True},
        {"title": "测试报告生成 Agent V1.0 需求文档", "obj_type": "docx", "node_token": "D1", "has_child": False},
        {"title": "UniResearch 相关账号及说明文档", "obj_type": "docx", "node_token": "ACC",
         "obj_token": "ACC_DOC", "has_child": True},
    ],
    # 版本目录层不得下探：里面的「测试环境」文档绝不能成为候选
    "V_NODE": [{"title": "测试环境部署说明", "obj_type": "docx", "node_token": "INVER", "has_child": False}],
    "ACC": [{"title": "Uniresearch测试环境及账号", "obj_type": "docx", "node_token": "ENVN",
             "obj_token": "ENV_DOC", "has_child": False}],
}


def test_collect_skips_version_dirs_and_finds_depth2():
    c = _stub_client(_TREE)
    cands = collect_env_candidates(c, "UniResearch", ["测试环境", "账号"])
    tokens = [n["node_token"] for n in cands]
    assert "ENVN" in tokens and "INVER" not in tokens
    assert "ACC" in tokens  # 父文档含「账号」也是候选，但 resolve 会解析失败继续试子文档


def test_collect_no_product_returns_empty():
    assert collect_env_candidates(_stub_client(_TREE), "不存在项目", ["账号"]) == []


def test_resolve_first_parseable_doc_wins():
    """逐份尝试命中即停：父文档（ACC_DOC）正文解析不出行组 → 继续试子文档（ENV_DOC）。"""
    c = _stub_client(_TREE)
    c.read_doc_raw_content = lambda doc_id: "" if doc_id == "ACC_DOC" else _ENV_DOC
    out = resolve_test_env(c, "UniResearch", ["测试环境", "账号"])
    assert out and out["rows"]["测试环境"]["url"] == "http://192.168.2.136:32012"
    assert out["source_title"] == "Uniresearch测试环境及账号"
    assert out["url"] == "https://x/wiki/ENVN"


def test_resolve_all_empty_returns_none():
    c = _stub_client(_TREE)
    c.read_doc_raw_content = lambda doc_id: "没有行组的散文"
    assert resolve_test_env(c, "UniResearch", ["测试环境", "账号"]) is None


def test_resolve_exception_degrades_none():
    c = _stub_client(_TREE)
    def boom(doc_id):
        raise RuntimeError("网络炸了")
    c.read_doc_raw_content = boom
    assert resolve_test_env(c, "UniResearch", ["账号"]) is None


# ---- FeishuClient.read_doc_raw_content（桩 _get）----

def _real_client():
    f = SimpleNamespace(api_base="https://open.feishu.cn", app_id="a", app_secret="b",
                        wiki_space_id="S", wiki_projects_parent="R", doc_base_url="https://x")
    return FeishuClient(f)


def test_read_raw_content_ok_and_degrade():
    c = _real_client()
    c._get = lambda path, params=None: {"data": {"content": "hello"}}
    assert c.read_doc_raw_content("DOC") == "hello"
    c._get = lambda path, params=None: (_ for _ in ()).throw(FeishuError("x", "timeout"))
    assert c.read_doc_raw_content("DOC") == ""   # 失败降级空串，不抛断
    assert c.read_doc_raw_content("") == ""      # 空 id 不发请求


# ---- renderer / 工具层：关闭与未命中时逐字一致 ----

def _stats():
    return StatsResult()


def test_renderer_without_env_identical():
    md = render_report(_stats())
    assert T.TEST_ENV_TABLE in md


def test_renderer_with_env_fills():
    env = {"rows": {"测试环境": {"url": "http://x.cn", "creds": "a/b"}}, "source_title": "TDoc"}
    md = render_report(_stats(), test_env=env)
    assert "| 测试环境 | http://x.cn | a/b |" in md
    assert "逐字引自知识库《TDoc》" in md
    assert "| 预发布环境 | 【待补充】 | 【待补充】 |" in md  # 未命中行给简洁占位


def test_tool_disabled_touches_nothing():
    cfg = SimpleNamespace(test_env=SimpleNamespace(enabled=False, doc_keywords=("账号",)),
                          feishu=SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                                                 wiki_space_id="S", wiki_projects_parent="R"))
    assert RenderReportTool(cfg)._resolve_test_env("UniResearch") is None
    assert RenderReportTool(None)._resolve_test_env("UniResearch") is None


def test_tool_run_meta_and_fill(monkeypatch):
    import report_agent.report.test_env as te
    monkeypatch.setattr(te, "resolve_test_env", lambda client, product, keywords:
                        {"rows": {"测试环境": {"url": "http://y.cn", "creds": "c/d"}},
                         "source_title": "S", "url": "https://x/wiki/N"})
    # 射掉检索侧真实网络（本用例只验环境链路）
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    cfg = SimpleNamespace(test_env=SimpleNamespace(enabled=True, doc_keywords=("账号",)),
                          feishu=SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                                                 wiki_space_id="S", wiki_projects_parent="R", doc_base_url="https://x"),
                          charts=SimpleNamespace(enabled=False, dpi=100), report_dir=".")
    res = RenderReportTool(cfg).run(stats_obj=_stats(), product_name="UniResearch", build="V5.0.0")
    assert res.ok and res.meta["test_env_filled"]
    assert "| 测试环境 | http://y.cn | c/d |" in res.data
