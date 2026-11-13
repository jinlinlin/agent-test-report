"""飞书导入后表格首行标记内置标题行（header_row）单测：目标选取/分页/分批/开关关闭零触碰/失败吞掉。

全离线：_get/_patch 用桩，不打网络。口径（2026-11-13 实测）：飞书 docx OpenAPI 无格级背景色，
唯一整格填充形态 = batch_update 子请求 update_table_property {header_row: true}，作用于 Table 块本身。
"""
import time
from types import SimpleNamespace

from report_agent.tools.feishu_client import FeishuClient, FeishuError
from report_agent.tools.push_feishu import PushFeishuTool
from report_agent.config import FeishuConfig


def _table(tid):
    return {"block_id": tid, "block_type": 31}


def _text(bid):
    return {"block_id": bid, "block_type": 2, "text": {"elements": [], "style": {}}}


class _FakeDocsClient(FeishuClient):
    """按 pages（分页 items 列表）应答 blocks GET，记录 batch_update PATCH。"""

    def __init__(self, pages):
        self._pages = pages  # [(items, has_more), ...]
        self.patches = []

    def _get(self, path, params=None, **kw):
        if path.endswith("/blocks"):
            token = (params or {}).get("page_token", "")
            idx = int(token) if token else 0
            items, has_more = self._pages[idx]
            data = {"items": items}
            if has_more:
                data.update({"has_more": True, "page_token": str(idx + 1)})
            return {"code": 0, "data": data}
        raise AssertionError(path)

    def _patch(self, path, body=None):
        self.patches.append((path, body))
        return {"code": 0, "data": {}}


def _patched_table_ids(client):
    ids = []
    for _path, body in client.patches:
        for req in body["requests"]:
            assert req["update_table_property"] == {"header_row": True}
            assert "update_text_style" not in req  # 不再走高亮条通道
            ids.append(req["block_id"])
    return ids


def test_marks_all_tables_across_pages(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _FakeDocsClient([([_table("t1"), _text("p1")], True), ([_table("t2")], False)])
    assert c.mark_table_header_rows("doc") == 2
    assert _patched_table_ids(c) == ["t1", "t2"]
    assert len(c.patches) == 1  # 每表一条请求，合一批


def test_no_tables_returns_zero_without_patch(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _FakeDocsClient([([_text("p1")], False)])
    assert c.mark_table_header_rows("doc") == 0
    assert c.patches == []


def test_splits_into_batches_of_200(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    items = [_table(f"t{i}") for i in range(201)]
    c = _FakeDocsClient([(items, False)])
    assert c.mark_table_header_rows("doc") == 201
    assert [len(body["requests"]) for _p, body in c.patches] == [200, 1]


# ---- push 接线：开关关闭零触碰 / 异常吞掉不阻断 ----

def test_push_disabled_makes_no_call():
    calls = []

    class _C:
        def mark_table_header_rows(self, doc):
            calls.append(doc)
            return 1

    ok = PushFeishuTool._mark_table_headers(_C(), SimpleNamespace(table_header_row=False), "d1")
    assert ok is False and calls == []


def test_push_exception_swallowed():
    class _C:
        def mark_table_header_rows(self, doc):
            raise FeishuError("boom")

    assert PushFeishuTool._mark_table_headers(_C(), SimpleNamespace(table_header_row=True), "d1") is False


def test_push_success_and_zero_tables_both_reported():
    class _One:
        def mark_table_header_rows(self, doc):
            return 3

    class _Zero:
        def mark_table_header_rows(self, doc):
            return 0

    assert PushFeishuTool._mark_table_headers(_One(), SimpleNamespace(table_header_row=True), "d1") is True
    assert PushFeishuTool._mark_table_headers(_Zero(), SimpleNamespace(table_header_row=True), "d1") is False


def test_config_default_on():
    assert FeishuConfig().table_header_row is True
