"""导入后表格列宽补打单测：版式匹配/请求构造/幂等跳过/未命中不动/开关关闭零触碰/异常吞掉。

全离线：_get/_patch 用桩，不打网络。口径（2026-11-13 实测）：docx 导入会把 column_width
重置为全表等宽默认值；列宽只能走单块更新 PATCH /blocks/{block_id} 的 update_table_property，
且逐列设置（column_index + 单个整数 column_width；传数组报 code=9499，batch_update 报 1770001）。
"""
import time
from types import SimpleNamespace

from report_agent.tools.feishu_client import FeishuClient, FeishuError
from report_agent.tools.push_feishu import PushFeishuTool
from report_agent.config import FeishuConfig


def _text_block(bid, content):
    return {"block_id": bid, "block_type": 2,
            "text": {"elements": [{"text_run": {"content": content}}]}}


def _cell(cid, text_id):
    return {"block_id": cid, "block_type": 32, "children": [text_id]}


def _table(tid, headers, widths=None):
    cols = len(headers)
    cells = [f"{tid}_c{i}" for i in range(cols)]
    tbl = {"block_id": tid, "block_type": 31,
           "table": {"property": {"column_size": cols, "row_size": 2,
                                  "column_width": widths or [330] * cols}},
           "children": cells}
    texts = [_text_block(f"{tid}_x{i}", h) for i, h in enumerate(headers)]
    blocks = [tbl] + [_cell(c, f"{tid}_x{i}") for i, c in enumerate(cells)] + texts
    return blocks


class _FakeClient(FeishuClient):
    def __init__(self, items):
        self._items = items
        self.patches = []

    def get_tenant_token(self):
        return "t"

    def _get(self, path, params=None, **kw):
        if path.endswith("/blocks"):
            return {"code": 0, "data": {"items": self._items}}
        raise AssertionError(path)

    def _patch(self, path, body=None):
        self.patches.append((path, body))
        return {"code": 0, "data": {}}


def _width_updates(client):
    """{block_id: [(column_index, column_width)]}：列宽补打走单块 PATCH，逐列一次。"""
    out: dict = {}
    for path, body in client.patches:
        assert path.startswith("/docx/v1/documents/doc/blocks/") and "batch_update" not in path
        bid = path.rsplit("/", 1)[-1]
        p = body["update_table_property"]
        out.setdefault(bid, []).append((p["column_index"], p["column_width"]))
    return {k: [w for _i, w in sorted(v)] for k, v in out.items()}


def test_scope_table_gets_proportional_widths(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _FakeClient(_table("t1", ["模块", "描述"]))
    assert c.set_table_column_widths("doc") is True
    assert _width_updates(c) == {"t1": [132, 528]}


def test_env_and_module_and_level_profiles(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    items = (_table("env", ["环境", "测试地址", "账号密码"], widths=[220, 220, 220])
             + _table("mod", ["模块", "缺陷总数", "已关闭", "待解决", "待验收", "延期"], widths=[110] * 6)
             + _table("lvl", ["缺陷等级", "缺陷性质", "系统中对应的错误分类", "描述", "修复优先级"], widths=[132] * 5))
    c = _FakeClient(items)
    assert c.set_table_column_widths("doc") is True
    assert dict(_width_updates(c)) == {
        "env": [112, 271, 277],
        "mod": [165, 99, 99, 99, 99, 99],
        "lvl": [86, 86, 132, 198, 158],
    }


def test_unmatched_table_untouched(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _FakeClient(_table("x", ["测试阶段", "开始时间", "结束时间"], widths=[220, 220, 220]))
    assert c.set_table_column_widths("doc") is False
    assert c.patches == []


def test_idempotent_skips_when_widths_already_applied(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _FakeClient(_table("t1", ["模块", "描述"], widths=[132, 528]))
    assert c.set_table_column_widths("doc") is False
    assert c.patches == []


# ---- push 接线：开关关闭零触碰 / 异常吞掉不阻断 ----

def test_push_disabled_flag_makes_no_call():
    calls = []

    class _C:
        def set_table_column_widths(self, doc):
            calls.append(doc)

    ok = PushFeishuTool._apply_table_column_widths(_C(), SimpleNamespace(table_column_widths=False), "d1")
    assert ok is False and calls == []


def test_push_exception_swallowed():
    class _C:
        def set_table_column_widths(self, doc):
            raise FeishuError("boom")

    assert PushFeishuTool._apply_table_column_widths(_C(), SimpleNamespace(table_column_widths=True), "d1") is False


def test_push_calls_client(monkeypatch):
    calls = []

    class _C:
        def set_table_column_widths(self, doc):
            calls.append(doc)
            return True

    ok = PushFeishuTool._apply_table_column_widths(_C(), SimpleNamespace(table_column_widths=True), "d1")
    assert ok is True and calls == ["d1"]


def test_config_default_enabled():
    assert FeishuConfig().table_column_widths is True
