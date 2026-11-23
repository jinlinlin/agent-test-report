"""2.1 三列形态（模块 | 分类 | 描述，scope.category_col，用户 2026-11-20 拍板）单测，全程不联网。

覆盖：
- 提取层：按 (所属模块, 模块分类) 二元组聚合、同名模块分属不同 owner 各自成行、
  合并单元格空值前向填充、无「所属模块」列行为与两列形态上线前逐字一致、
  表头只有「所属模块分类」（与模块列命中同一列）时视为无 owner 列；
- 归并：组跨多个所属模块按 owner 拆行、各自相加不丢数；
- apply_display_columns：组名剥「owner+分隔符」前缀，前缀不同整名保留；
- scope_table 三列渲染：表头、同模块首行写名续行留空（伪合并）、owner 相邻重排、
  top_modules 收敛尾行、两列模式逐字不变；
- 工具层链路：category_col 开 + 用表带所属模块 → 报告出三列表；用表无该列 → 回退两列。
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import openpyxl

from report_agent.report.cases import (
    apply_display_columns, apply_module_groups, extract_scope_from_rows,
)
from report_agent.report.templates import scope_table
from report_agent.schemas.contracts import StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool


def _rows(data, header=("用例编号", "所属模块", "模块分类", "用例名称", "执行结果")):
    return [list(header)] + [list(r) for r in data]


# ---------- 提取层 ----------

def test_owner_tuple_key_splits_same_module_across_owners():
    rows = _rows([
        ["TC-1", "云文档", "Excel文档", "a", "通过"],
        ["TC-2", "第三方集成", "Excel文档", "b", "失败"],
    ])
    scope = extract_scope_from_rows(rows)
    assert [(r["owner"], r["module"], r["case_count"]) for r in scope] == [
        ("云文档", "Excel文档", 1), ("第三方集成", "Excel文档", 1)]


def test_owner_forward_fill_for_merged_cells():
    # 合并单元格经 sheets 取值只有首格有值：空值应沿用最近的非空所属模块
    rows = _rows([
        ["TC-1", "云文档", "Excel文档", "a", ""],
        ["TC-2", "", "Word文档", "b", ""],
    ])
    scope = extract_scope_from_rows(rows)
    assert [r["owner"] for r in scope] == ["云文档", "云文档"]


def test_no_owner_column_rows_have_no_owner_key():
    rows = [["用例编号", "模块分类", "用例名称"], ["TC-1", "Excel文档", "a"]]
    scope = extract_scope_from_rows(rows)
    assert scope and all("owner" not in r for r in scope)


def test_owner_header_matching_same_column_as_module_is_ignored():
    # 表头只有「所属模块分类」：owner 与 module 命中同一列 → 视为无独立所属模块列（旧行为）
    rows = [["编号", "所属模块分类", "标题"], ["1", "文献管理-导入", "x"]]
    scope = extract_scope_from_rows(rows)
    assert scope and all("owner" not in r for r in scope)
    assert scope[0]["module"] == "文献管理"


# ---------- 归并拆行 ----------

def test_module_groups_split_group_row_by_owner():
    scope = [
        {"module": "Excel文档", "desc": "d1", "case_count": 3, "exec_available": True,
         "owner": "云文档", "passed": 2, "failed": 0, "blocked": 0, "not_run": 1, "other": 0},
        {"module": "Excel文档", "desc": "d1", "case_count": 1, "exec_available": True,
         "owner": "第三方集成", "passed": 0, "failed": 1, "blocked": 0, "not_run": 0, "other": 0},
        {"module": "Word文档", "desc": "d2", "case_count": 2, "exec_available": True,
         "owner": "云文档", "passed": 1, "failed": 0, "blocked": 0, "not_run": 1, "other": 0},
    ]
    out = apply_module_groups(scope, [("云文档-文档类型与管理", ("Excel文档", "Word文档"))])
    group_rows = [r for r in out if r["module"] == "云文档-文档类型与管理"]
    assert [(r["owner"], r["case_count"], r["desc"]) for r in group_rows] == [
        ("云文档", 5, "Excel文档、Word文档"), ("第三方集成", 1, "Excel文档")]
    assert group_rows[0]["passed"] == 3 and group_rows[1]["failed"] == 1


# ---------- 展示字段派生 ----------

def test_display_columns_strip_owner_prefix_only():
    out = apply_display_columns([
        {"module": "云文档-文档类型与管理", "owner": "云文档"},
        {"module": "第三方集成-飞书应用", "owner": "云文档"},   # 前缀不同 → 整名保留（用户例表一致）
        {"module": "散点模块", "owner": "云文档"},              # 无该前缀 → 整名保留
        {"module": "旧表行"},                                   # 无 owner → owner 空串、category=整名
    ])
    assert [(r["owner"], r["category"]) for r in out] == [
        ("云文档", "文档类型与管理"),
        ("云文档", "第三方集成-飞书应用"),
        ("云文档", "散点模块"),
        ("", "旧表行"),
    ]


# ---------- 三列渲染 ----------

def _three_row(owner, category, desc, cc=1):
    return {"module": category, "owner": owner, "category": category, "desc": desc,
            "case_count": cc, "exec_available": False}


def test_scope_table_three_col_pseudo_merge_and_regroup():
    rows = [_three_row("云文档", "文档类型与管理", "d1", 9),
            _three_row("第三方集成", "飞书应用", "d2", 8),
            _three_row("云文档", "分享与协作", "d3", 7)]
    md = scope_table(rows, three_col=True)
    lines = md.splitlines()
    assert lines[0] == "| 模块 | 分类 | 描述 |"
    # owner 首次出现序重排：云文档两行相邻且第二行模块列留空（伪合并）
    assert lines[2] == "| 云文档 | 文档类型与管理 | d1 |"
    assert lines[3] == "|  | 分享与协作 | d3 |"
    assert lines[4] == "| 第三方集成 | 飞书应用 | d2 |"


def test_scope_table_three_col_tail_row():
    rows = [_three_row("云文档", f"分类{i}", f"d{i}", 10 - i) for i in range(4)]
    md = scope_table(rows, top_modules=2, three_col=True)
    assert "| 其他（2 个分类） | — | 分类2、分类3 |" in md


def test_scope_table_two_col_verbatim_unchanged():
    rows = [_three_row("云文档", "云文档-文档类型与管理", "d1")]
    md = scope_table(rows)  # 默认两列：不读 owner/category
    assert md.splitlines()[0] == "| 模块 | 描述 |"
    assert "| 云文档-文档类型与管理 | d1 |" in md


# ---------- 工具层链路 ----------

def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cfg(category_col=True):
    feishu = SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(
        feishu=feishu,
        charts=SimpleNamespace(enabled=False, dpi=100),
        report_dir=".",
        scope=SimpleNamespace(enabled=True, case_doc_title="测试用例", module_col="模块分类",
                              name_col="用例名称", group_level="top", top_modules=10,
                              module_groups=(("云文档-文档类型与管理", ("Excel文档", "Word文档")),),
                              exec_stats=False, exec_col="执行结果",
                              category_col=category_col, owner_col="所属模块"),
    )


def _stub_client(monkeypatch, data):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "F1" if obj_type == "file" else "")
    monkeypatch.setattr(FeishuClient, "download_file", lambda self, tok: data)


def test_tool_renders_three_col_with_owner_grounding(monkeypatch):
    data = _xlsx([
        ["用例编号", "所属模块", "模块分类", "用例名称"],
        ["TC-1", "云文档", "Excel文档", "a"],
        ["TC-2", "云文档", "Word文档", "b"],
    ])
    _stub_client(monkeypatch, data)
    res = RenderReportTool(_cfg()).run(stats_obj=StatsResult(), product_name="P", build="V1")
    assert res.meta["test_scope_three_col"] is True
    assert "| 模块 | 分类 | 描述 |" in res.data
    assert "| 云文档 | 文档类型与管理 | Excel文档、Word文档 |" in res.data


def test_tool_falls_back_two_col_without_owner_column(monkeypatch):
    data = _xlsx([["用例编号", "模块分类", "用例名称"], ["TC-1", "Excel文档", "a"]])
    _stub_client(monkeypatch, data)
    res = RenderReportTool(_cfg()).run(stats_obj=StatsResult(), product_name="P", build="V1")
    assert res.meta["test_scope_three_col"] is False
    assert "| 模块 | 描述 |" in res.data
    assert "| 模块 | 分类 | 描述 |" not in res.data


def test_tool_switch_off_keeps_two_col_even_with_owner(monkeypatch):
    data = _xlsx([["用例编号", "所属模块", "模块分类", "用例名称"], ["TC-1", "云文档", "Excel文档", "a"]])
    _stub_client(monkeypatch, data)
    res = RenderReportTool(_cfg(category_col=False)).run(stats_obj=StatsResult(), product_name="P", build="V1")
    assert res.meta["test_scope_three_col"] is False
    assert "| 模块 | 描述 |" in res.data
