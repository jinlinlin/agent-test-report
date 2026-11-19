"""V2-4 延伸：人工确认的「一级菜单 → 成员模块」映射归并（cases.apply_module_groups）单测与链路测试。

背景：用例表把「模块分类」填成扁平名时，一级聚合会产出几十行菜单。归并映射写在 settings.yaml
（scope.module_groups），程序只做**模块名全等**归并：成员名逐字来自用例表，未命中的模块原样单列，
不发明组名、不丢数据。本文件覆盖归并规则、链路透传与配置数据完整性（全离线，不联网）。
"""
from __future__ import annotations

from types import SimpleNamespace

from report_agent.config import load_config
from report_agent.report.cases import apply_module_groups
from report_agent.schemas.contracts import ModuleDist, StatsResult
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.render_report import RenderReportTool

_GROUPS = (
    ("云文档-文档类型与管理", ("Excel文档", "Word文档", "云文档类型扩展")),
    ("云文档-分享与协作", ("指定协作者模式", "分享设置弹窗")),
)


def _scope():
    return [{"module": "云文档类型扩展", "desc": "新建入口展示", "case_count": 4},
            {"module": "Excel文档", "desc": "Sheet数量", "case_count": 26},
            {"module": "指定协作者模式", "desc": "搜索区", "case_count": 10},
            {"module": "分享设置弹窗", "desc": "权限下拉", "case_count": 6}]


def test_groups_collapse_rows_and_sum_counts():
    out = apply_module_groups(_scope(), _GROUPS)
    assert [r["module"] for r in out] == ["云文档-文档类型与管理", "云文档-分享与协作"]
    # 描述=成员模块名逐字连接，顺序按用例表出现顺序而非映射声明顺序
    assert out[0]["desc"] == "云文档类型扩展、Excel文档"
    assert out[0]["case_count"] == 30 and out[1]["case_count"] == 16
    assert "Sheet数量" not in out[0]["desc"]  # 原功能点不堆进组行，只留成员名


def test_unmatched_module_kept_as_own_row_after_groups():
    """映射没覆盖到的模块绝不丢：原样附在组行之后（含它自己的功能点描述）。"""
    scope = _scope() + [{"module": "新模块", "desc": "新功能点", "case_count": 3}]
    out = apply_module_groups(scope, _GROUPS)
    assert [r["module"] for r in out][-1] == "新模块"
    assert out[-1]["desc"] == "新功能点" and out[-1]["case_count"] == 3


def test_member_in_two_groups_only_first_wins():
    """同一成员被多个组声明→只归首个组，绝不重复计数。"""
    groups = (("A组", ("Excel文档",)), ("B组", ("Excel文档", "分享设置弹窗")))
    out = apply_module_groups(_scope(), groups)
    assert out[0]["module"] == "A组" and out[0]["case_count"] == 26
    assert out[1]["module"] == "B组" and out[1]["desc"] == "分享设置弹窗" and out[1]["case_count"] == 6
    # 总数守恒：归并前后用例数相加不变（Excel文档 没被两个组各算一次）
    assert sum(r["case_count"] for r in out) == sum(r["case_count"] for r in _scope())


def test_exact_match_only_never_fuzzy():
    """全等才归并：「Excel文档集」不能被「Excel文档」模糊命中。"""
    scope = [{"module": "Excel文档集", "desc": "x", "case_count": 5}]
    out = apply_module_groups(scope, _GROUPS)
    assert [r["module"] for r in out] == ["Excel文档集"]


def test_group_with_no_hit_is_dropped_not_fabricated():
    """组内一个模块也没命中→整行不出现（不写出空组冒充范围）。"""
    groups = (("空组", ("不存在的模块",)), ("云文档-分享与协作", ("分享设置弹窗",)))
    out = apply_module_groups(_scope(), groups)
    names = [r["module"] for r in out]
    assert "空组" not in names
    assert names == ["云文档-分享与协作", "云文档类型扩展", "Excel文档", "指定协作者模式"]
    assert out[0]["desc"] == "分享设置弹窗" and out[0]["case_count"] == 6


def test_empty_mapping_returns_scope_unchanged():
    scope = _scope()
    assert apply_module_groups(scope, ()) is scope
    assert apply_module_groups(scope, None) is scope
    assert apply_module_groups(None, _GROUPS) is None


def test_bad_count_does_not_break_grouping():
    out = apply_module_groups([{"module": "Excel文档", "desc": "x", "case_count": None},
                               {"module": "Word文档", "desc": "y"}], _GROUPS)
    assert out[0]["module"] == "云文档-文档类型与管理" and out[0]["case_count"] == 0


# ---- 链路透传：配置里的映射要真的改变 2.1 输出 ----


def _stats(*modules):
    s = StatsResult()
    s.module_distribution = [ModuleDist(module=m) for m in modules]
    return s


def _cfg(module_groups=()):
    feishu = SimpleNamespace(enabled=True, app_id="a", app_secret="b",
                             wiki_space_id="S", wiki_projects_parent="ROOT", doc_base_url="https://x")
    return SimpleNamespace(
        feishu=feishu,
        charts=SimpleNamespace(enabled=False, dpi=100),
        report_dir=".",
        scope=SimpleNamespace(enabled=True, case_doc_title="测试用例", module_col="模块分类",
                              name_col="用例名称", group_level="top", top_modules=0,
                              module_groups=module_groups),
    )


_ROWS = [["模块分类", "用例名称"],
         ["Excel文档", "Excel文档-Sheet数量-新增"],
         ["云文档类型扩展", "云文档类型扩展-新建入口-主页面"],
         ["分享设置弹窗", "分享设置弹窗-权限下拉-协作者"],
         ["零散模块", "零散模块-冒烟-主流程"]]


def test_render_tool_applies_configured_groups(monkeypatch):
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s1"])
    monkeypatch.setattr(FeishuClient, "read_sheet_rows", lambda self, tok, sheet_id="", **kw: _ROWS)
    res = RenderReportTool(_cfg(_GROUPS)).run(stats_obj=_stats("Excel文档"), product_name="UniResearch", build="V5.2.0")
    lines = [ln for ln in res.data.split("## 2.1 测试范围")[1].split("## 2.2")[0].split("\n") if ln.startswith("| ")]
    assert lines[2] == "| 云文档-文档类型与管理 | Excel文档、云文档类型扩展 |"  # 按用例表行序，不是映射声明序
    assert lines[3] == "| 云文档-分享与协作 | 分享设置弹窗 |"
    assert lines[4] == "| 零散模块 | 冒烟 |"  # 未覆盖模块仍单列，不丢
    assert res.meta["test_scope_modules"] == 3


def test_render_tool_without_groups_unchanged(monkeypatch):
    """映射为空→与归并上线前逐字一致（细模块各自成行）。"""
    monkeypatch.setattr(FeishuClient, "find_doc_urls", lambda self, p, b, t: {})
    monkeypatch.setattr(FeishuClient, "find_doc_obj_token",
                        lambda self, p, b, t, obj_type="": "SHTOK" if obj_type == "sheet" else "")
    monkeypatch.setattr(FeishuClient, "list_sheet_ids", lambda self, tok: ["s1"])
    monkeypatch.setattr(FeishuClient, "read_sheet_rows", lambda self, tok, sheet_id="", **kw: _ROWS)
    res = RenderReportTool(_cfg()).run(stats_obj=_stats("Excel文档"), product_name="UniResearch", build="V5.2.0")
    assert "| Excel文档 | Sheet数量 |" in res.data and "云文档-文档类型与管理" not in res.data


# ---- 生产配置的数据完整性（拍板值防手滑）----


def test_settings_module_groups_are_consistent():
    groups = load_config().scope.module_groups
    assert [name for name, _ in groups] == ["云文档-文档类型与管理", "云文档-分享与协作",
                                            "第三方集成-飞书应用", "第三方集成-文档同步"]
    members = [m for _n, ms in groups for m in ms]
    assert len(members) == len(set(members)) == 33  # 无重复声明，覆盖 V5.2.0 全部 33 个模块分类
    assert all(name and ms for name, ms in groups)
