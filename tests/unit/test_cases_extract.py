"""V2-4 数据源 D：测试用例 xlsx 解析纯函数单测（内存构造 xlsx，不联网）。

覆盖：一级模块聚合与计数、full 粒度、表头模糊匹配、模块列缺失/空/坏字节回退 None、分隔符变体。
"""
from __future__ import annotations

import io

import openpyxl

from report_agent.report.cases import extract_scope_from_cases, extract_scope_from_rows


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_aggregate_top_level_with_counts():
    data = _xlsx([
        ["用例编号", "模块分类", "用例名称"],
        ["TC-1", "AI对话组件-模型选择", "a"],
        ["TC-2", "AI对话组件-对话模式", "b"],
        ["TC-3", "研究助手-问答", "c"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["AI对话组件", "研究助手"]
    # 描述=该菜单测到的功能点（二级段），非用例数
    assert rows[0]["desc"] == "模型选择、对话模式" and rows[0]["case_count"] == 2
    assert rows[1]["desc"] == "问答"


def test_full_group_level_keeps_paths():
    data = _xlsx([
        ["模块分类", "用例名称"],
        ["AI对话组件-模型选择", "下拉展示"],
        ["AI对话组件-对话模式", "极速切换"],
    ])
    rows = extract_scope_from_cases(data, group_level="full")
    assert [r["module"] for r in rows] == ["AI对话组件-模型选择", "AI对话组件-对话模式"]
    assert rows[0]["desc"] == "下拉展示"


def test_module_col_fuzzy_match():
    data = _xlsx([["编号", "所属模块分类", "标题"], ["1", "文献管理-导入", "x"]])
    rows = extract_scope_from_cases(data, module_col="模块分类")
    assert rows and rows[0]["module"] == "文献管理"


def test_preserves_first_seen_order():
    data = _xlsx([
        ["模块分类"],
        ["研究助手-问答"],
        ["AI对话组件-模型"],
        ["研究助手-溯源"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["研究助手", "AI对话组件"]
    assert rows[0]["desc"] == "问答、溯源" and rows[0]["case_count"] == 2


def test_blank_and_none_cells_skipped():
    data = _xlsx([["模块分类"], ["文献管理-导入"], [None], [""]])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["文献管理"]


def test_no_module_column_returns_none():
    data = _xlsx([["用例编号", "用例名称"], ["TC-1", "x"]])
    assert extract_scope_from_cases(data) is None


def test_header_only_no_data_returns_none():
    data = _xlsx([["用例编号", "模块分类"]])
    assert extract_scope_from_cases(data) is None


def test_empty_bytes_returns_none():
    assert extract_scope_from_cases(b"") is None


def test_corrupt_bytes_returns_none():
    assert extract_scope_from_cases(b"not an xlsx at all") is None


def test_name_path_fallback_uses_second_segment():
    """模块分类只有一级时，描述退用「用例名称」的二级段，不堆整条多级路径。"""
    data = _xlsx([
        ["模块分类", "用例名称"],
        ["研究探索", "研究探索-页面重构-旧的类型单选tab已移除"],
        ["研究探索", "研究探索-意图识别-研究类问题走研究探索链路"],
        ["异常与边界", "异常边界-输入超长文本-发送处理"],
    ])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["研究探索", "异常与边界"]
    assert rows[0]["desc"] == "页面重构、意图识别"
    assert rows[1]["desc"] == "输入超长文本"


def test_alternative_separators():
    data = _xlsx([["模块分类"], ["数据分析／可视化"], ["数据分析/图表"], ["研究助手>问答"]])
    rows = extract_scope_from_cases(data)
    assert [r["module"] for r in rows] == ["数据分析", "研究助手"]


# ---- 行矩阵入口（飞书原生电子表格 obj_type=sheet 数据源）----

def test_rows_entry_equals_xlsx_entry_for_same_data():
    """同一份数据走两个入口结果逐字相等（解耦不改口径）。"""
    table = [["用例编号", "模块分类", "用例名称"],
             ["TC-1", "AI对话组件-模型选择", "下拉展示"],
             ["TC-2", "AI对话组件-对话模式", "极速切换"],
             ["TC-3", "研究助手-问答", "问答"]]
    from_rows = extract_scope_from_rows(table)
    from_xlsx = extract_scope_from_cases(_xlsx(table))
    assert from_rows == from_xlsx
    assert [r["module"] for r in from_rows] == ["AI对话组件", "研究助手"]


def test_rows_column_order_irrelevant():
    """按表头名定位列：sheet 里「优先级」排在第 8 列（xlsx 里第 4 列）不影响提取。"""
    rows = extract_scope_from_rows([
        ["用例编号", "模块分类", "用例名称", "测试类型", "前置条件", "测试步骤", "预期结果", "优先级", "执行结果"],
        ["TC-001", "云文档类型扩展", "云文档类型扩展-新建入口展示-主页面", "功能测试", "x", "y", "z", "P1", ""],
        ["TC-005", "Excel文档", "Excel文档-Sheet数量-持续新增", "功能测试", "x", "y", "z", "P1", "成功"],
    ])
    assert [r["module"] for r in rows] == ["云文档类型扩展", "Excel文档"]
    # 模块分类为扁平一级 → 描述退用「用例名称」二级功能点
    assert rows[0]["desc"] == "新建入口展示" and rows[1]["desc"] == "Sheet数量"


def test_rows_empty_returns_none():
    assert extract_scope_from_rows([]) is None


def test_rows_without_module_col_returns_none():
    assert extract_scope_from_rows([["用例编号", "用例名称"], ["TC-1", "x"]]) is None
