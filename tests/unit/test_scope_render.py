"""V2-4 测试范围接地进入渲染链路的集成单测（桩替换飞书用例表下载，不联网）。

覆盖：
- 开关关（含 cfg=None）：2.1 保持【待补充】占位，与 V1 逐字一致，不定位/下载用例表；
- 开关开 + 用例 xlsx 命中：2.1 渲染真实范围表（模块=菜单目录，描述=功能点）；
- 开关开但未定位到用例表：整体回退占位。
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import openpyxl

from report_agent.schemas.contracts import ModuleDist, StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool


def _stats(*modules):
    s = StatsResult()
    s.module_distribution = [ModuleDist(module=m) for m in modules]
    return s


def _cfg(scope_enabled=True):
    feishu = SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(
        feishu=feishu,
        charts=SimpleNamespace(enabled=False, dpi=100),
        report_dir=".",
        scope=SimpleNamespace(enabled=scope_enabled,
                              case_doc_title="测试用例", module_col="模块分类", name_col="用例名称", group_level="top"),
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
    """未定位到用例 xlsx（无 file 节点）→ 2.1 回退占位。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "")
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("AI对话组件"), product_name="UniResearch", build="V5.1.0")
    assert res.meta["test_scope_filled"] is False
    assert "【待补充，可参照 4.1.1 模块分布】" in res.data
