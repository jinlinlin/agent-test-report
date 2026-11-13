# -*- coding: utf-8 -*-
"""V2-1 候选工具 list_version_candidates 单测（mock，不联网）。

钉死三条不变量（对应 B1）：
- 候选按版本创建时间倒序、「最新」= candidates[0] 由程序排序决定（非模型裁量）；
- 项目隔离：列 UniResearch 不混入一表通，反之亦然；
- 项目名含混走「先精确后包含」；未知项目返回空候选。
"""
from report_agent.config import load_config
from report_agent.tools.mock_zentao import list_candidates
from report_agent.tools.version_candidates import ListVersionCandidatesTool, _version_sort_key

BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False, "charts.enabled": False}


def _builds(result_data):
    return [c["build"] for c in result_data["candidates"]]


def test_uniresearch_candidates_sorted_latest_first():
    data = list_candidates("UniResearch").data
    assert data["product"] == "UniResearch"
    # 按 build_created 倒序：V6.1.0(11-05) > V6.0.0(10-25) > V5.0.0(09-25)
    assert _builds(data) == ["V6.1.0", "V6.0.0", "V5.0.0"]
    # 「最新」口径钉死为首位
    assert data["candidates"][0]["build"] == "V6.1.0"


def test_no_cross_project_contamination():
    uni = _builds(list_candidates("UniResearch").data)
    ybt = _builds(list_candidates("一表通项目").data)
    assert set(uni).isdisjoint(ybt)
    assert ybt == ["V1.3.0", "V1.2.0"]  # 一表通自身按创建时间倒序


def test_partial_hint_matches_by_contains():
    # 只给「一表通」，靠包含匹配命中「一表通项目」
    data = list_candidates("一表通").data
    assert data["product"] == "一表通项目"
    assert len(data["candidates"]) == 2


def test_unknown_product_returns_empty_candidates():
    res = list_candidates("不存在的平台")
    assert res.ok is True and res.data["candidates"] == []


def test_tool_run_mock_path():
    tool = ListVersionCandidatesTool(load_config(**BASE))
    res = tool.run(product_hint="UniResearch")
    assert res.ok and res.data["candidates"][0]["build"] == "V6.1.0"


def test_version_sort_key_semantic_order():
    # 版本号语义排序：6.6.7.2 > 6.6.7 > 3.2.0；无数字排最小（空元组）
    keys = [_version_sort_key(b) for b in ["V6.6.7.2", "V6.6.7", "V3.2.0", "abc"]]
    assert sorted(keys, reverse=True) == [(6, 6, 7, 2), (6, 6, 7), (3, 2, 0), ()]
