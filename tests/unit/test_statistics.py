"""统计工具单元测试（对应需求文档 8.2 的 U-xxx）。"""
from report_agent.tools.statistics import (
    collect_people,
    compute_statistics,
    status_bucket,
    level_of,
    opened_date_range,
    collect_defect_lists,
)


def _bug(module, severity, status, resolution, activated=0):
    return {"module": module, "severity": severity, "status": status,
            "resolution": resolution, "activatedCount": activated}


def test_u001_empty_list():
    """U-001：空缺陷列表 → 全 0，覆盖率 0.00%，不抛异常。"""
    r = compute_statistics({"name": "V", "fetch_status": "ok", "test_start": "a", "test_end": "b"}, [])
    assert r.summary.total == 0
    assert r.summary.fix_coverage == "0.00%"
    assert r.conclusion.p1_pass is True


def test_u006_postponed_in_denominator():
    """U-006：延期计入总数分母，不计入已关闭，覆盖率两位小数。"""
    bugs = [
        _bug("/A", 3, "closed", "fixed"),
        _bug("/A", 4, "resolved", "postponed"),
    ]
    r = compute_statistics({"name": "V", "fetch_status": "ok", "test_start": "a", "test_end": "b"}, bugs)
    assert r.summary.total == 2
    assert r.summary.closed == 1
    assert r.summary.postponed == 1
    assert r.summary.fix_coverage == "50.00%"


def test_u007_resolution_mapping():
    """U-007：状态映射按 3.2 定义归类（已关闭优先，延期只认 postponed）。"""
    # 待解决：status=激活
    assert status_bucket(_bug("/A", 3, "active", "")) == "pending_resolve"
    # 待验收：status=已解决 且 解决方案≠延期处理
    assert status_bucket(_bug("/A", 3, "resolved", "fixed")) == "pending_accept"
    assert status_bucket(_bug("/A", 2, "resolved", "willnotfix")) == "pending_accept"
    # 延期：status≠已关闭 且 解决方案=延期处理
    assert status_bucket(_bug("/A", 4, "resolved", "postponed")) == "postponed"
    # 已关闭：status=已关闭（无论 resolution，优先级高于延期）
    assert status_bucket(_bug("/A", 3, "closed", "fixed")) == "closed"
    assert status_bucket(_bug("/A", 4, "closed", "willnotfix")) == "closed"
    assert status_bucket(_bug("/A", 4, "closed", "postponed")) == "closed"
    assert status_bucket(_bug("/A", 3, "closed", "duplicate")) == "closed"


def test_u008_module_grouping_no_merge():
    """U-008：模块按路径原样分组，不合并。"""
    bugs = [
        _bug("/ 科研管理 / 材料生成", 3, "closed", "fixed"),
        _bug("/ 科研管理 / 成果管理", 3, "closed", "fixed"),
        _bug("/ 科研管理 / 材料生成", 2, "active", ""),
    ]
    r = compute_statistics({"name": "V", "fetch_status": "ok", "test_start": "a", "test_end": "b"}, bugs)
    mods = {m.module: m for m in r.module_distribution}
    assert "/ 科研管理 / 材料生成" in mods and "/ 科研管理 / 成果管理" in mods
    assert mods["/ 科研管理 / 材料生成"].total == 2


def test_level_of_mapping():
    assert level_of(1) == "P1" and level_of(4) == "P4"


def test_activated_rate():
    bugs = [_bug("/A", 3, "closed", "fixed", activated=2), _bug("/A", 3, "closed", "fixed", activated=0)]
    r = compute_statistics({"name": "V", "fetch_status": "ok", "test_start": "a", "test_end": "b"}, bugs)
    assert r.summary.activated_ge1_count == 1
    assert r.summary.activated_rate == "50.00%"


def _bug_dated(module, severity, status, resolution, opened):
    b = _bug(module, severity, status, resolution)
    b["openedDate"] = opened
    return b


def test_u009_test_time_from_opened_date_range():
    """U-009：测试起止时间由本版本缺陷创建时间(openedDate)的 min/max 实算，优先于占位值（UTC→北京跨日）。"""
    bugs = [
        _bug_dated("/A", 3, "closed", "fixed", "2026-10-05T01:30:00Z"),   # 北京 2026-10-05 09:30
        _bug_dated("/A", 3, "active", "", "2026-09-28T18:00:00Z"),           # 北京 2026-09-29 02:00
        _bug_dated("/A", 2, "resolved", "fixed", "2026-10-01T12:00:00Z"),
    ]
    r = compute_statistics({"name": "V", "fetch_status": "ok", "test_start": "", "test_end": ""}, bugs)
    assert r.version.test_start == "2026-09-29"   # 最早
    assert r.version.test_end == "2026-10-05"      # 最晚
    assert "测试起止时间" not in r.integrity.missing_fields


def test_u010_test_time_fallback_when_no_dates():
    """U-010：缺陷无可用创建时间时，回退到上游传入的配置/占位值。"""
    r = compute_statistics(
        {"name": "V", "fetch_status": "ok", "test_start": "2026-01-01", "test_end": "2026-02-02"},
        [_bug("/A", 3, "closed", "fixed")],
    )
    assert r.version.test_start == "2026-01-01" and r.version.test_end == "2026-02-02"


def test_opened_date_range_handles_null():
    """空/null/零值日期均丢弃，无可用日期时返回空串。"""
    bugs = [{"openedDate": "0000-00-00 00:00:00"}, {"openedDate": ""}, {"openedDate": None}]
    assert opened_date_range(bugs) == ("", "")


def test_u011_test_time_only_exact_build():
    """U-011：测试起止只取影响版本恰好等于目标版本的缺陷，排除多值带入（如 'V5.0.0, V5.1.0'）的历史缺陷。"""
    bugs = [
        {"module": "/A", "severity": 3, "status": "closed", "resolution": "fixed", "activatedCount": 0,
         "openedBuild": "V5.1.0", "openedDate": "2026-03-01T00:00:00Z"},          # 精确匹配，计入
        {"module": "/A", "severity": 3, "status": "closed", "resolution": "fixed", "activatedCount": 0,
         "openedBuild": "V5.0.0, V5.1.0", "openedDate": "2025-12-10T00:00:00Z"},  # 多值带入，排除
        {"module": "/A", "severity": 2, "status": "active", "resolution": "", "activatedCount": 0,
         "openedBuild": "V5.1.0", "openedDate": "2026-03-20T00:00:00Z"},          # 精确匹配，计入
    ]
    r = compute_statistics({"name": "UniResearch V5.1.0", "build": "V5.1.0",
                            "fetch_status": "ok", "test_start": "", "test_end": ""}, bugs)
    assert r.version.test_start == "2026-03-01"   # 而非被多值缺陷拖到 2025-12-10
    assert r.version.test_end == "2026-03-20"
    assert r.summary.total == 3                    # 总数仍按包含即计入（由上游已过滤），不受时间口径影响


def test_collect_people_exact_version_only():
    """仅当前版本：openedBy/resolvedBy 去重，多值带入缺陷不计入；空值不入选。"""
    bugs = [
        {"openedBuild": "V5.1.0", "openedBy": "tester_a", "resolvedBy": "dev_wang"},
        {"openedBuild": "V5.1.0", "openedBy": "tester_a", "resolvedBy": "dev_zhao"},   # 提交人重复
        {"openedBuild": "V5.1.0", "openedBy": "tester_b", "resolvedBy": ""},           # 未解决，无解决人
        {"openedBuild": "V5.0.0, V5.1.0", "openedBy": "tester_x", "resolvedBy": "dev_x"},  # 多值带入，排除
    ]
    p = collect_people(bugs, "V5.1.0")
    assert p["reporters"] == ["tester_a", "tester_b"]
    assert p["developers"] == ["dev_wang", "dev_zhao"]


def test_compute_statistics_people_into_31():
    """compute_statistics 将本版本提交/解决人写入 people，供 3.1 渲染。"""
    bugs = [
        {"module": "/A", "severity": 3, "status": "closed", "resolution": "fixed",
         "activatedCount": 0, "openedBuild": "V5.1.0", "openedBy": "tester_a", "resolvedBy": "dev_wang"},
        {"module": "/A", "severity": 3, "status": "active", "resolution": "",
         "activatedCount": 0, "openedBuild": "V5.1.0", "openedBy": "tester_b", "resolvedBy": ""},
    ]
    r = compute_statistics({"name": "UniResearch V5.1.0", "build": "V5.1.0", "fetch_status": "ok"}, bugs)
    assert r.people.test_lead == ["tester_a", "tester_b"]
    assert r.people.testers == ["tester_a", "tester_b"]
    assert r.people.developers == ["dev_wang"]
    assert r.people.project_owner == ""   # 未取到时留空，渲染层标待补充


def test_render_31_marks_placeholder_when_no_project_owner():
    """渲染层：有提交/解决人则填入，项目负责人缺失时标【待补充】。"""
    from report_agent.report.renderer import render_report
    bugs = [
        {"module": "/A", "severity": 3, "status": "closed", "resolution": "fixed",
         "activatedCount": 0, "openedBuild": "V5.1.0", "openedBy": "tester_a", "resolvedBy": "dev_wang"},
    ]
    r = compute_statistics({"name": "UniResearch V5.1.0", "build": "V5.1.0", "fetch_status": "ok"}, bugs)
    md = render_report(r)
    assert "| 测试负责人 | tester_a |" in md
    assert "| 软件工程师（开发、运维） | dev_wang |" in md
    assert "项目负责人 | 【待补充" in md


def test_collect_defect_lists_predicates():
    """激活=status active；延期=postponed且未关闭；closed+postponed 不算延期；resolved+fixed 两表都不进。"""
    bugs = [
        {"id": 1, "status": "active", "resolution": ""},
        {"id": 2, "status": "resolved", "resolution": "postponed"},
        {"id": 3, "status": "closed", "resolution": "postponed"},   # 已关闭，不进延期表
        {"id": 4, "status": "resolved", "resolution": "fixed"},      # 两表都不进
    ]
    active, postponed = collect_defect_lists(bugs)
    assert [r.id for r in active] == [1]
    assert [r.id for r in postponed] == [2]


def test_defect_lists_counts_match_summary():
    """清单条数与 summary 的待解决/延期计数对齐（同口径）。"""
    bugs = [
        {"module": "/A", "severity": 3, "status": "active", "resolution": "", "activatedCount": 0},
        {"module": "/A", "severity": 3, "status": "active", "resolution": "", "activatedCount": 1},
        {"module": "/B", "severity": 2, "status": "resolved", "resolution": "postponed", "activatedCount": 0},
        {"module": "/C", "severity": 3, "status": "closed", "resolution": "fixed", "activatedCount": 0},
    ]
    r = compute_statistics({"name": "V", "fetch_status": "ok"}, bugs)
    assert len(r.active_bugs) == r.summary.pending_resolve == 2
    assert len(r.postponed_bugs) == r.summary.postponed == 1


def test_defect_table_sanitizes_and_maps():
    """渲染：去 HTML 标签、竖线转义、枚举中文化；空清单返回提示语。"""
    from report_agent.report.renderer import _defect_table, _clean_cell
    from report_agent.schemas.contracts import BugRow
    assert _clean_cell("<p>a | b</p>") == "a ｜ b"
    row = BugRow(id=9, module="/M", title="T", priority=2, type="codeerror",
                 steps="<div>步骤1</div>", status="active", opened_by="张三",
                 opened_date="2026-11-09T02:01:28Z", opened_build="V5.1.0",
                 assigned_to="李四", resolution="postponed")
    md = _defect_table([row], "无")
    assert "Bug编号" in md and "解决方案" in md
    assert "| 1 | 9 | /M | T | 2 | 代码错误 | 步骤1 | 激活 | 张三 | 2026-11-09 | V5.1.0 | 李四 | 延期处理 |" in md
    assert _defect_table([], "本版本无激活缺陷。") == "本版本无激活缺陷。"
