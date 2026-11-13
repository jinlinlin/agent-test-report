"""V2-3 第 1 步：项目级参考文档链接记忆 DocLinkStore 单测。

锁住用户最在意的「复用一定要是正确的」相关不变量：
- 严格按项目隔离（新项目/新类型无记录返回 None，绝不跨项目借用）；
- 同项目不同写法（大小写/多余空格）收敛到同一格；
- 登记前校验 URL 合法性，乱码/非链接不入库；
- 命中即覆盖；持久化跨实例可读；文件缺失/损坏静默降级为空。
"""
from __future__ import annotations

import json

from report_agent.memory.doc_links import DocLinkStore


def test_register_and_lookup_roundtrip(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    ok = store.register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/ABC123")
    assert ok is True
    got = store.lookup("UniResearch", "产品原型文档")
    assert got == {"url": "https://feishu.cn/wiki/ABC123", "title": "产品原型文档"}


def test_unknown_project_or_type_returns_none(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/ABC")
    # 新项目、新类型都没有记录 → None（交上层首次询问），绝不借用别的格子
    assert store.lookup("一表通项目", "产品原型文档") is None
    assert store.lookup("UniResearch", "需求文档") is None


def test_project_isolation_no_cross_leak(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("UniResearch", "需求文档", "https://feishu.cn/wiki/U1")
    store.register("一表通项目", "需求文档", "https://feishu.cn/wiki/Y1")
    assert store.lookup("UniResearch", "需求文档")["url"] == "https://feishu.cn/wiki/U1"
    assert store.lookup("一表通项目", "需求文档")["url"] == "https://feishu.cn/wiki/Y1"


def test_project_key_normalization_collapses(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("  UniResearch ", "UI 设计文档", "https://feishu.cn/wiki/X")
    # 大小写 / 首尾空格不同的同一项目写法应落到同一格
    assert store.lookup("uniresearch", "UI 设计文档") is not None
    assert store.lookup("UNIRESEARCH", "UI 设计文档")["url"] == "https://feishu.cn/wiki/X"


def test_invalid_url_rejected_not_persisted(tmp_path):
    p = tmp_path / "doc_links.json"
    store = DocLinkStore(p)
    for bad in ["", "   ", "ftp://x", "javascript:alert(1)", "not a url", "/wiki/rel"]:
        assert store.register("UniResearch", "软件用户手册", bad) is False
    assert store.lookup("UniResearch", "软件用户手册") is None
    assert not p.exists()  # 一条都没写成功，不应产生文件


def test_empty_product_or_type_rejected(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    assert store.register("", "需求文档", "https://feishu.cn/wiki/x") is False
    assert store.register("UniResearch", "   ", "https://feishu.cn/wiki/x") is False


def test_register_overwrites_existing(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/OLD")
    store.register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/NEW")
    assert store.lookup("UniResearch", "产品原型文档")["url"] == "https://feishu.cn/wiki/NEW"


def test_persist_across_instances(tmp_path):
    p = tmp_path / "doc_links.json"
    DocLinkStore(p).register("UniResearch", "需求文档", "https://feishu.cn/wiki/P")
    # 新实例从磁盘重新加载
    assert DocLinkStore(p).lookup("UniResearch", "需求文档")["url"] == "https://feishu.cn/wiki/P"


def test_custom_title_used_in_lookup(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("UniResearch", "需求文档", "https://feishu.cn/wiki/T", title="UniResearch 需求规格 V5")
    assert store.lookup("UniResearch", "需求文档")["title"] == "UniResearch 需求规格 V5"


def test_all_for_project_lists_valid_entries(tmp_path):
    store = DocLinkStore(tmp_path / "doc_links.json")
    store.register("UniResearch", "需求文档", "https://feishu.cn/wiki/a")
    store.register("UniResearch", "产品原型文档", "https://feishu.cn/wiki/b")
    store.register("一表通项目", "需求文档", "https://feishu.cn/wiki/other")
    listed = store.all_for_project("UniResearch")
    assert set(listed) == {"需求文档", "产品原型文档"}
    assert listed["需求文档"]["url"] == "https://feishu.cn/wiki/a"


def test_missing_file_is_empty(tmp_path):
    store = DocLinkStore(tmp_path / "nope.json")
    assert store.lookup("UniResearch", "需求文档") is None
    assert store.all_for_project("UniResearch") == {}


def test_corrupt_file_degrades_to_empty(tmp_path):
    p = tmp_path / "doc_links.json"
    p.write_text("{ this is not json", encoding="utf-8")
    store = DocLinkStore(p)  # 不抛异常
    assert store.lookup("UniResearch", "需求文档") is None


def test_corrupt_entry_ignored_on_lookup(tmp_path):
    p = tmp_path / "doc_links.json"
    p.write_text(json.dumps({"uniresearch": {"需求文档": {"url": "http://x"}}}), encoding="utf-8")
    # scheme=http 且无有效主机名场景（此处 host=x 合法）；再放一条明显非法的
    data = json.loads(p.read_text(encoding="utf-8"))
    data["uniresearch"]["产品原型文档"] = {"url": "garbage"}
    p.write_text(json.dumps(data), encoding="utf-8")
    store = DocLinkStore(p)
    assert store.lookup("UniResearch", "产品原型文档") is None      # 非法记录被忽略
    assert store.lookup("UniResearch", "需求文档")["url"] == "http://x"  # 合法记录仍可用
