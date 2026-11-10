"""发布建议规则引擎单元测试（对应需求文档 8.2 U-002~U-005）。"""
from report_agent.rules.release_engine import evaluate
from report_agent.schemas.contracts import LevelStatus


def _dist(p1=0, p2=0, p3=0, p4=0):
    return [
        LevelStatus(level="P1", active=p1),
        LevelStatus(level="P2", active=p2),
        LevelStatus(level="P3", active=p3),
        LevelStatus(level="P4", active=p4),
    ]


def test_u002_all_pass_when_no_active():
    c = evaluate(_dist(0, 0, 0, 0), total=100)
    assert c.p1_pass and c.p2_pass and c.p3_pass
    assert "可按计划发布" in c.release_advice


def test_total_zero_pass():
    c = evaluate(_dist(0, 0, 0, 0), total=0)
    assert c.p1_pass and c.p2_pass and c.p3_pass
    assert "未发现缺陷" in c.release_advice


def test_u003_active_p1_blocks():
    c = evaluate(_dist(p1=1), total=100)
    assert c.p1_pass is False
    assert "暂缓发布" in c.release_advice


def test_u004_p2_boundary():
    # 5/100 = 5.00% 通过；6/100 = 6.00% 不通过
    assert evaluate(_dist(p2=5), total=100).p2_pass is True
    assert evaluate(_dist(p2=6), total=100).p2_pass is False


def test_u005_p3_boundary():
    # 30/100 = 30.00% 通过；31/100 = 31.00% 不通过
    assert evaluate(_dist(p3=30), total=100).p3_pass is True
    assert evaluate(_dist(p3=31), total=100).p3_pass is False


def test_p4_not_limited():
    c = evaluate(_dist(p4=50), total=100)
    assert c.p4_limited is False
    assert c.p1_pass and c.p2_pass and c.p3_pass


def test_evidence_present():
    c = evaluate(_dist(p2=5), total=100)
    assert len(c.evidence) == 4
