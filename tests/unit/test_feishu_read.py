"""V2-4 飞书读取侧只读能力的单测（桩替换导航原语，不联网）：定位版本目录下指定类型节点。

覆盖 find_doc_obj_token：
- obj_type 过滤：只认指定类型（如 file），跳过同名异类型节点（思维导图/文档），避免下载到不可读节点；
- 严格「项目+版本」作用域：产品/版本/标题任一缺失返回空（绝不跨版本/项目猜）。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.tools.feishu_client import FeishuClient


def _client():
    f = SimpleNamespace(wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x.feishu.cn")
    return FeishuClient(f)


def _stub(client, project="P_NODE", version="V_NODE", nodes=None):
    client.find_child_by_title = lambda space, parent, name: project if name == "UniResearch" else ""
    client.find_child_node = lambda space, parent, build: version if (parent == "P_NODE" and build == "V5.0.0") else ""
    client.list_nodes = lambda space, parent: nodes if parent == "V_NODE" else []


# ---- find_doc_obj_token ----

def test_find_obj_token_matches_requested_obj_type():
    c = _client()
    _stub(c, nodes=[
        {"title": "测试用例.xlsx", "obj_type": "docx", "obj_token": "DOCX_VER"},
        {"title": "测试用例.xlsx", "obj_type": "file", "obj_token": "FILE_RAW"},
    ])
    # 下载用例表须精确锁定 file 附件，不被同名 docx 抢先
    assert c.find_doc_obj_token("UniResearch", "V5.0.0", "测试用例", obj_type="file") == "FILE_RAW"


def test_find_obj_token_skips_other_types():
    c = _client()
    _stub(c, nodes=[{"title": "测试用例", "obj_type": "mindnote", "obj_token": "MIND"}])
    # 目录下只有思维导图（无 file 版）→ 返回空，不拿不可下载的 obj_token
    assert c.find_doc_obj_token("UniResearch", "V5.0.0", "测试用例", obj_type="file") == ""


def test_find_obj_token_title_absent_returns_empty():
    c = _client()
    _stub(c, nodes=[{"title": "会议纪要", "obj_type": "file", "obj_token": "X"}])
    assert c.find_doc_obj_token("UniResearch", "V5.0.0", "测试用例", obj_type="file") == ""


def test_find_obj_token_no_cross_version():
    c = _client()
    _stub(c, version="")  # 版本目录不存在
    c.list_nodes = lambda space, parent: [{"title": "测试用例", "obj_type": "file", "obj_token": "LEAK"}]
    assert c.find_doc_obj_token("UniResearch", "V9.9.9", "测试用例", obj_type="file") == ""


def test_find_obj_token_no_cross_project():
    c = _client()
    _stub(c, project="")  # 产品节点匹配不到
    assert c.find_doc_obj_token("OtherProduct", "V5.0.0", "测试用例", obj_type="file") == ""


def test_find_obj_token_requires_product_and_build():
    c = _client()
    assert c.find_doc_obj_token("", "V5.0.0", "测试用例", obj_type="file") == ""
    assert c.find_doc_obj_token("UniResearch", "", "测试用例", obj_type="file") == ""
