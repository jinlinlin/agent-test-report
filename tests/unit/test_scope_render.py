"""V2-4 测试范围接地进入渲染链路的集成单测（桩替换飞书用例表下载/取值，不联网）。

覆盖：
- 开关关（含 cfg=None）：2.1 保持【待补充】占位，与 V1 逐字一致，不定位/不读用例表；
- 开关开 + 用例 xlsx（obj_type=file）命中：2.1 渲染真实范围表（模块=菜单目录，描述=功能点）；
- 开关开 + 只有飞书原生电子表格（obj_type=sheet）：走 sheets 取值接口同样填充，file 共存时优先 file；
  多子表依次尝试不合并；
- 开关开但未定位到用例表/取值为空：整体回退占位；
- top_modules 收敛：模块数超阈值时按用例数降序取前 N + 「其他」合并行（用例数只用于排序、不上表），未超/为 0 时原文顺序全列。
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import openpyxl

from report_agent.report import templates as T
from report_agent.schemas.contracts import ModuleDist, StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool


def _stats(*modules):
    s = StatsResult()
    s.module_distribution = [ModuleDist(module=m) for m in modules]
    return s


def _cfg(scope_enabled=True, top_modules=0):
    feishu = SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(
        feishu=feishu,
        charts=SimpleNamespace(enabled=False, dpi=100),
        report_dir=".",
        scope=SimpleNamespace(enabled=scope_enabled,
                              case_doc_title="测试用例", module_col="模块分类", name_col="用例名称",
                              group_level="top", top_modules=top_modules),
    )


def _cases_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["用例编号", "模块分类", "用例名称"])
    ws.append(["TC-001", "AI对话组件-模型选择", "下拉展示"])
    ws.append(["TC-002", "AI对话组件-对话模式", "极速切换"])
    ws.append(["TC-003", "研究助手-问答", "问答"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_disabled_keeps_placeholder_and_downloads_nothing(monkeypatch):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    calls = []
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": calls.append(t) or "")
    res = RenderReportTool(_cfg(scope_enabled=False)).run(
        stats_obj=_stats("AI对话组件"), product_name="UniResearch", build="V5.0.0")
    assert res.ok and res.meta["test_scope_filled"] is False
    assert calls == []  # 关闭时绝不定位/下载用例表
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data


def test_cfg_none_backward_compatible():
    res = RenderReportTool(None).run(stats_obj=_stats("AI对话组件"), product_name="X", build="V1")
    assert res.ok and res.meta["test_scope_filled"] is False
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data


def test_cases_xlsx_fills_scope(monkeypatch):
    data = _cases_xlsx()
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "FTOK" if obj_type == "file" else "")
    monkeypatch.setattr(FeishuClient, "download_file", lambda self, tok: data)
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("AI对话组件"), product_name="UniResearch", build="V5.1.0")
    assert res.meta["test_scope_filled"] is True
    assert "| AI对话组件 | 模型选择、对话模式 |" in res.data
    assert "| 研究助手 | 问答 |" in res.data


def test_no_cases_file_falls_back(monkeypatch):
    """未定位到用例表（无 file 也无 sheet 节点）→ 2.1 回退占位。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "")
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("AI对话组件"), product_name="UniResearch", build="V5.1.0")
    assert res.meta["test_scope_filled"] is False
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data


# ---- 飞书原生电子表格（obj_type=sheet）数据源 ----

_SHEET_ROWS = [
    ["用例编号", "模块分类", "用例名称", "测试类型", "前置条件", "测试步骤", "预期结果", "优先级", "执行结果"],
    ["TC-001", "云文档类型扩展", "云文档类型扩展-新建入口展示-主页面", "功能测试", "", "", "", "P1", ""],
    ["TC-002", "云文档类型扩展", "云文档类型扩展-文档类型图标-三类新增", "功能测试", "", "", "", "P1", ""],
    ["TC-005", "Excel文档", "Excel文档-Sheet数量-持续新增", "功能测试", "", "", "", "P1", "成功"],
]


def test_sheet_fills_scope(monkeypatch):
    """只有原生电子表格（无 .xlsx 附件）时，走 sheets 取值接口仍能填充 2.1。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s1"])
    monkeypatch.setattr(FeishuClient, "read_sheet_rows", lambda self, tok, sheet_id="", **kw: _SHEET_ROWS)
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("云文档类型扩展"), product_name="UniResearch", build="V5.2.0")
    assert res.meta["test_scope_filled"] is True
    assert "| 云文档类型扩展 | 新建入口展示、文档类型图标 |" in res.data
    assert "| Excel文档 | Sheet数量 |" in res.data


def test_sheet_multi_subsheet_tries_in_order_not_merge(monkeypatch):
    """多子表依次尝试、取首个能解析出模块的子表，不跨子表合并。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s_cover", "s_cases", "s_extra"])
    read = []
    tables = {"s_cover": [["项目", "负责人"], ["UniResearch", "张三"]],      # 无模块列→提不出
              "s_cases": _SHEET_ROWS,                                        # 命中
              "s_extra": [["用例编号", "模块分类"], ["TC-9", "不该被合并"]]}   # 不应再读
    monkeypatch.setattr(FeishuClient, "read_sheet_rows",
                        lambda self, tok, sheet_id="", **kw: read.append(sheet_id) or tables.get(sheet_id, []))
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("云文档类型扩展"), product_name="UniResearch", build="V5.2.0")
    assert read == ["s_cover", "s_cases"]  # 命中即停，不读第三个子表也不合并
    assert res.meta["test_scope_filled"] is True
    assert "不该被合并" not in res.data


def test_file_preferred_over_sheet(monkeypatch):
    """同一目录两种载体共存→以 file 附件为准，不调 sheets 接口（历史行为不变）。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "FTOK" if obj_type == "file" else "SHTOK")
    monkeypatch.setattr(FeishuClient, "download_file", lambda self, tok: _cases_xlsx())
    read = []
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: read.append("ids") or ["s1"])
    monkeypatch.setattr(FeishuClient, "read_sheet_rows",
                        lambda self, tok, sheet_id="", **kw: read.append(sheet_id) or _SHEET_ROWS)
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("AI对话组件"), product_name="UniResearch", build="V5.1.0")
    assert res.meta["test_scope_filled"] is True
    assert read == []  # 命中 file 就绝不再去读 sheet
    assert "| AI对话组件 | 模型选择、对话模式 |" in res.data


def test_sheet_empty_rows_falls_back(monkeypatch):
    """定位到 sheet 但取值为空（无权限/接口异常降级）→ 回退占位，不编造范围。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s1", "s2"])
    monkeypatch.setattr(FeishuClient, "read_sheet_rows", lambda self, tok, sheet_id="", **kw: [])
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("云文档类型扩展"), product_name="UniResearch", build="V5.2.0")
    assert res.meta["test_scope_filled"] is False
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data


def test_sheet_without_subsheet_ids_does_not_read(monkeypatch):
    """列不出子表（无权限/接口异常）→ 直接回退占位，不拿空 sheet_id 去重复查询。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: [])
    read = []
    monkeypatch.setattr(FeishuClient, "read_sheet_rows",
                        lambda self, tok, sheet_id="", **kw: read.append(sheet_id) or _SHEET_ROWS)
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("云文档类型扩展"), product_name="UniResearch", build="V5.2.0")
    assert read == [] and res.meta["test_scope_filled"] is False
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data


# ---- top_modules 行收敛（表头保持两列，用例数只用于排序不上表）----


def _many_modules(n, base=1):
    """造 n 个模块，用例数依次递增（首个模块用例数最少，用来验证排序不是原序）。"""
    return [{"module": f"M{i:02d}", "desc": f"功能点{i}", "case_count": base + i} for i in range(n)]


def test_template_top_modules_merges_tail():
    rows = _many_modules(12)  # M00=1 条 … M11=12 条
    md = T.scope_table(rows, top_modules=5)
    lines = md.split("\n")
    assert lines[0] == "| 模块 | 描述 |"
    body = lines[2:]
    assert len(body) == 6  # 5 行真实模块 + 1 行其他
    assert body[0] == "| M11 | 功能点11 |"  # 按用例数降序，不再是用例表原序
    assert body[4] == "| M07 | 功能点7 |"
    assert body[5] == "| 其他（7 个模块） | M06、M05、M04、M03、M02、M01、M00 |"
    assert "12" not in md and "用例数" not in md  # 用例数绝不应上表


def test_template_under_threshold_keeps_original_order():
    rows = _many_modules(3)
    md = T.scope_table(rows, top_modules=10)
    assert "其他（" not in md
    assert md.split("\n")[2:] == ["| M00 | 功能点0 |", "| M01 | 功能点1 |", "| M02 | 功能点2 |"]


def test_template_zero_keeps_all_rows_and_original_order():
    rows = _many_modules(30)
    md = T.scope_table(rows, top_modules=0)
    assert "其他（" not in md
    assert len(md.split("\n")) == 32  # 表头 2 行 + 30 行模块
    assert "| M00 | 功能点0 |" in md and "| M29 | 功能点29 |" in md


def test_template_without_case_count_keeps_original_order():
    """case_count 缺失（旧调用方只传 module/desc）：不报错，同分维持原序。"""
    rows = [{"module": "甲", "desc": "点"}, {"module": "乙", "desc": "点"}]
    md = T.scope_table(rows, top_modules=5)
    assert md.split("\n")[2:] == ["| 甲 | 点 |", "| 乙 | 点 |"]


def test_template_tail_names_truncated_to_eight():
    md = T.scope_table(_many_modules(20), top_modules=1)
    tail = md.split("\n")[-1]
    assert tail.startswith("| 其他（19 个模块） | M18、M17、M16、M15、M14、M13、M12、M11…")


def test_render_tool_passes_top_modules(monkeypatch):
    """配置里的 top_modules 一路透传到渲染（集成链路，不只模板单测）。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s1"])
    rows = [["模块分类", "用例名称"]] + [[f"M{i:02d}", f"M{i:02d}-点"] for i in range(12)]
    monkeypatch.setattr(FeishuClient, "read_sheet_rows", lambda self, tok, sheet_id="", **kw: rows)
    res = RenderReportTool(_cfg(top_modules=5)).run(
        stats_obj=_stats("M00"), product_name="UniResearch", build="V5.2.0")
    assert res.meta["test_scope_filled"] is True
    assert res.meta["test_scope_modules"] == 12
    assert "其他（7 个模块）" in res.data


def test_settings_yaml_enables_top_modules():
    """生产配置拍板值：2.1 最多单列 10 个模块（防扁平模块分类刷屏）。"""
    from report_agent.config import load_config
    assert load_config().scope.top_modules == 10
