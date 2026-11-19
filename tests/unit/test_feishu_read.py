"""V2-4 飞书读取侧只读能力的单测（桩替换导航原语/HTTP 层，不联网）。

覆盖 find_doc_obj_token：
- obj_type 过滤：只认指定类型（如 file），跳过同名异类型节点（思维导图/文档），避免下载到不可读节点；
- 严格「项目+版本」作用域：产品/版本/标题任一缺失返回空（绝不跨版本/项目猜）。

覆盖 read_sheet_rows / list_sheet_ids（飞书原生电子表格取值）：
- 子表发现与区间拼接；空 token/无子表/接口异常一律降级空列表，绝不抛断主链路；
- 多窗续读（单窗读满且末行非空才续）、max_windows 封顶、已指定子表时不额外请求 query。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.tools.feishu_client import FeishuClient, FeishuError


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


# ---- read_sheet_rows（原生电子表格取值）----

def _sheet_client(calls, payload):
    """把 _get 桩成记录调用路径+返回固定 payload 的形式，验证两步只读而不联网。"""
    c = _client()
    c._get = lambda path, params=None: (calls.append((path, params)), dict(payload))[1]
    return c


def test_read_sheet_rows_two_step_navigation():
    calls = []
    payload = {"data": {"sheets": [{"sheet_id": "0cduFq", "title": "测试用例"}]}}
    # 第二步返回行矩阵（第一步同样返回该 payload，sheets 字段对第二步无害）
    payload["data"]["valueRange"] = {"values": [["用例编号", "模块分类"], ["TC-1", "云文档类型扩展"]]}
    c = _sheet_client(calls, payload)
    rows = c.read_sheet_rows("SHEET_TOK", max_rows=2000, max_cols=15)
    assert rows == [["用例编号", "模块分类"], ["TC-1", "云文档类型扩展"]]
    assert calls[0][0] == "/sheets/v3/spreadsheets/SHEET_TOK/sheets/query"
    assert calls[1][0] == "/sheets/v2/spreadsheets/SHEET_TOK/values/0cduFq!A1:O2000"
    assert calls[1][1] == {"valueRenderOption": "ToString"}


def test_read_sheet_rows_column_label_over_z():
    calls = []
    c = _sheet_client(calls, {"data": {"sheets": [{"sheet_id": "s1"}], "valueRange": {"values": [["x"]]}}})
    c.read_sheet_rows("T", max_rows=10, max_cols=28)
    assert calls[1][0].endswith("/values/s1!A1:AB10")


def test_read_sheet_rows_empty_token_returns_empty():
    c = _client()
    c._get = lambda path, params=None: (_ for _ in ()).throw(AssertionError("不得发请求"))
    assert c.read_sheet_rows("") == []


def test_read_sheet_rows_no_subsheet_returns_empty():
    c = _sheet_client([], {"data": {"sheets": []}})
    assert c.read_sheet_rows("T") == []


def test_read_sheet_rows_api_error_degrades_to_empty():
    c = _client()

    def _boom(path, params=None):
        raise FeishuError("无权限", "error")

    c._get = _boom
    assert c.read_sheet_rows("T") == []


def test_read_sheet_rows_explicit_sheet_id_skips_query():
    """已指定子表→不再调 query，只读一次取值区间。"""
    calls = []
    c = _sheet_client(calls, {"data": {"valueRange": {"values": [["x"]]}}})
    assert c.read_sheet_rows("T", sheet_id="sX", max_rows=5, max_cols=3) == [["x"]]
    assert len(calls) == 1
    assert calls[0][0] == "/sheets/v2/spreadsheets/T/values/sX!A1:C5"


def test_read_sheet_rows_continues_to_next_window():
    """单窗读满且末行非空→续读下一窗，行拼接且起始行递增。"""
    calls = []
    c = _client()
    windows = [[[f"h{i}"] for i in range(2)], [["r2"], ["r3"]]]

    def _get(path, params=None):
        calls.append(path)
        if "sheets/query" in path:
            return {"data": {"sheets": [{"sheet_id": "s1"}]}}
        return {"data": {"valueRange": {"values": windows.pop(0) if windows else []}}}

    c._get = _get
    rows = c.read_sheet_rows("T", max_rows=2, max_cols=1, max_windows=3)
    assert [r[0] for r in rows] == ["h0", "h1", "r2", "r3"]
    assert calls[1].endswith("/values/s1!A1:A2")
    assert calls[2].endswith("/values/s1!A3:A4")


def test_read_sheet_rows_stops_at_max_windows():
    """一直读满也不会无限续：最多 max_windows 个窗。"""
    calls = []
    c = _client()

    def _get(path, params=None):
        calls.append(path)
        if "sheets/query" in path:
            return {"data": {"sheets": [{"sheet_id": "s1"}]}}
        return {"data": {"valueRange": {"values": [["a"], ["b"]]}}}

    c._get = _get
    rows = c.read_sheet_rows("T", max_rows=2, max_cols=1, max_windows=2)
    assert len(rows) == 4 and len(calls) == 3  # 1 次 query + 2 个窗


def test_read_sheet_rows_stops_when_last_row_blank():
    """末行全空→视为已到表尾，不再续读下一窗。"""
    calls = []
    c = _client()

    def _get(path, params=None):
        calls.append(path)
        if "sheets/query" in path:
            return {"data": {"sheets": [{"sheet_id": "s1"}]}}
        return {"data": {"valueRange": {"values": [["a"], ["", ""]]}}}

    c._get = _get
    rows = c.read_sheet_rows("T", max_rows=2, max_cols=1, max_windows=3)
    assert len(rows) == 2 and len(calls) == 2


# ---- list_sheet_ids ----

def test_list_sheet_ids_returns_all_ids():
    c = _client()
    c._get = lambda path, params=None: {"data": {"sheets": [{"sheet_id": "a1"}, {"sheet_id": ""}, {"title": "x"}]}}
    assert c.list_sheet_ids("T") == ["a1"]


def test_list_sheet_ids_empty_token_and_error():
    c = _client()
    assert c.list_sheet_ids("") == []

    def _boom(path, params=None):
        raise FeishuError("无权限", "error")

    c._get = _boom
    assert c.list_sheet_ids("T") == []
