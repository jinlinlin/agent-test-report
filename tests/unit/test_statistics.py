"""统计工具单元测试（对应需求文档 8.2 的 U-xxx）。"""
from report_agent.tools.statistics import compute_statistics, status_bucket, level_of


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
