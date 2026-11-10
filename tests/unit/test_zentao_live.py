"""禅道 live 路径单元测试：用假客户端注入，验证解析/归一化/异常映射，无需真实服务器。"""
import pytest

from report_agent.config import load_config
from report_agent.tools import zentao_client as zc
from report_agent.tools.zentao_bugs import GetVersionBugsTool
from report_agent.tools.zentao_version import GetVersionInfoTool

BASE = {"llm.use_llm_planner": False, "zentao.mode": "live", "zentao.base_url": "https://zt.test",
        "zentao.account": "ro", "zentao.password": "pw", "trace.enabled": False}


class FakeClient:
    """替换 ZentaoRestClient：按预置数据/异常返回。"""
    product = None
    raw_bugs = []
    raise_exc = None

    def __init__(self, cfg):
        self.cfg = cfg

    def find_product(self, version_name):
        if self.raise_exc:
            raise self.raise_exc
        return self.product

    def get_product_bugs(self, product_id):
        if self.raise_exc:
            raise self.raise_exc
        return self.raw_bugs

    def get_bug(self, bug_id):
        # 详情接口：本测试不返回 moduleTitle，验证回退到列表接口的 module 字符串
        return None


@pytest.fixture
def fake(monkeypatch):
    FakeClient.product = None
    FakeClient.raw_bugs = []
    FakeClient.raise_exc = None
    monkeypatch.setattr(zc, "ZentaoRestClient", FakeClient)
    return FakeClient


def test_version_live_ok(fake):
    fake.product = {"id": 7, "name": "UniResearch V5.0.0", "desc": "科研管理增强"}
    cfg = load_config(**BASE)
    r = GetVersionInfoTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is True
    assert r.data["fetch_status"] == "ok"
    assert r.data["name"] == "UniResearch V5.0.0"
    assert r.data["description"] == "科研管理增强"
    assert r.data["product_id"] == 7
    # 时间字段先占位
    assert r.data["test_start"] == "" and r.data["test_end"] == ""


def test_version_live_not_found(fake):
    fake.product = None
    cfg = load_config(**BASE)
    r = GetVersionInfoTool(cfg).run(version_name="UniResearch V9.9.9")
    assert r.ok is False
    assert r.meta["fetch_status"] == "not_found"


def test_version_live_timeout(fake):
    fake.raise_exc = zc.ZentaoError("禅道接口超时", "timeout")
    cfg = load_config(**BASE)
    r = GetVersionInfoTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is False
    assert r.meta["fetch_status"] == "timeout"


def test_bugs_live_normalize(fake):
    fake.product = {"id": 7, "name": "UniResearch V5.0.0"}
    fake.raw_bugs = [
        {"id": 1, "module": "/ 科研管理", "severity": 1, "status": "closed", "resolution": "fixed", "activatedCount": 2, "openedBuild": "V5.0.0"},
        {"id": 2, "module": "/ 科研实验", "severity": 2, "status": "active", "resolution": "", "activatedCount": 0, "openedBuild": "V5.0.0"},
        {"id": 3, "module": "", "severity": 4, "status": "resolved", "resolution": "postponed", "openedBuild": "V4.0.0,V5.0.0"},
    ]
    cfg = load_config(**BASE)
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is True
    assert r.meta["count"] == 3
    b = r.data
    assert b[0]["severity"] == 1 and b[0]["status"] == "closed"
    assert b[1]["resolution"] == ""
    # 缺 module 兜底为「未分类」，缺 activatedCount 兜底为 0
    assert b[2]["module"] == "未分类"
    assert b[2]["activatedCount"] == 0


def test_bugs_live_excludes_other_version(fake):
    """跨版本带入口径：影响版本不含本版本的缺陷应被排除。"""
    fake.product = {"id": 7, "name": "UniResearch"}
    fake.raw_bugs = [
        {"id": 1, "module": "/ A", "severity": 3, "status": "active", "resolution": "", "activatedCount": 0, "openedBuild": "V5.0.0"},
        {"id": 2, "module": "/ B", "severity": 3, "status": "active", "resolution": "", "activatedCount": 0, "openedBuild": "V5.1.0"},
        {"id": 3, "module": "/ C", "severity": 2, "status": "closed", "resolution": "fixed", "activatedCount": 1, "openedBuild": "V5.0.0,V5.1.0"},
    ]
    cfg = load_config(**BASE)
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is True
    assert r.meta["count"] == 2  # id2(V5.1.0) 被排除；id3 跨版本含 V5.0.0 计入
    assert {b["id"] for b in r.data} == {1, 3}


def test_bugs_live_version_not_found(fake):
    """V1-002 live：指定版本号在产品全部缺陷 openedBuild 中均不存在 → 判定版本不存在（ok=False/not_found）。"""
    fake.product = {"id": 7, "name": "UniResearch"}
    fake.raw_bugs = [
        {"id": 1, "module": "/ A", "severity": 3, "status": "active", "resolution": "", "activatedCount": 0, "openedBuild": "V5.0.0"},
        {"id": 2, "module": "/ B", "severity": 3, "status": "closed", "resolution": "fixed", "activatedCount": 0, "openedBuild": "V5.1.0,V5.0.0"},
    ]
    cfg = load_config(**BASE)
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V0.9.1")
    assert r.ok is False
    assert r.meta["fetch_status"] == "not_found"
    assert "版本不存在" in (r.error or "")


def test_bugs_live_empty_but_version_exists(fake):
    """版本号在 openedBuild 集合中存在（即使本例子只 1 条）应正常返回，不误判为不存在。"""
    fake.product = {"id": 7, "name": "UniResearch"}
    fake.raw_bugs = [
        {"id": 1, "module": "/ A", "severity": 3, "status": "active", "resolution": "", "activatedCount": 0, "openedBuild": "V0.9.1"},
    ]
    cfg = load_config(**BASE)
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V0.9.1")
    assert r.ok is True
    assert r.meta["count"] == 1


def test_bugs_live_custom_field_mapping(fake, monkeypatch):
    """字段名走配置：模拟禅道返回非标准字段名。"""
    fake.product = {"id": 9, "name": "UniResearch V5.0.0"}
    fake.raw_bugs = [{"bid": 1, "moduleName": "/ A", "sev": 3, "st": "active", "res": "", "act": 1, "openedBuild": "V5.0.0"}]
    overrides = {**BASE, "zentao.field_module": "moduleName", "zentao.field_severity": "sev",
                 "zentao.field_status": "st", "zentao.field_resolution": "res", "zentao.field_activated": "act"}
    cfg = load_config(**overrides)
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is True
    assert r.data[0]["module"] == "/ A"
    assert r.data[0]["severity"] == 3
    assert r.data[0]["activatedCount"] == 1


def test_bugs_live_error(fake):
    fake.product = {"id": 7, "name": "UniResearch V5.0.0"}
    fake.raise_exc = zc.ZentaoError("禅道接口返回 500", "error")
    cfg = load_config(**BASE)
    # find_product 抛错 → error
    r = GetVersionBugsTool(cfg).run(version_name="UniResearch V5.0.0")
    assert r.ok is False
    assert r.meta["fetch_status"] == "error"


def test_parse_version():
    """版本全名拆为产品关键字 + 影响版本 token。"""
    assert zc.parse_version("UniResearch V5.0.0") == ("UniResearch", "V5.0.0")
    assert zc.parse_version("UniResearch 5.0") == ("UniResearch", "5.0")
    assert zc.parse_version("UniResearch") == ("UniResearch", None)
    # 多段（4 段）版本号不得被截断
    assert zc.parse_version("助睿数智服务平台产品 V6.6.7.2") == ("助睿数智服务平台产品", "V6.6.7.2")


def test_extract_version_name_multisegment():
    """自然语言抽取版本号时，4 段版本号必须完整保留（回归：曾被截成 3 段）。"""
    from report_agent.agent.planner import extract_version_name
    assert extract_version_name("帮我生成 助睿数智服务平台产品 V6.6.7.2 的测试报告") == "助睿数智服务平台产品 V6.6.7.2"
    assert extract_version_name("UniResearch V5.1.0") == "UniResearch V5.1.0"


def test_build_matches_contains_semantics():
    """影响版本“包含即计入”：逗号串中含本版本即命中，忽略大小写，部分版本号不误匹配。"""
    assert zc.build_matches("V5.0.0", "V5.0.0") is True
    assert zc.build_matches("V5.0.0,V5.1.0", "V5.0.0") is True
    assert zc.build_matches("v5.0.0", "V5.0.0") is True
    assert zc.build_matches("V5.1.0", "V5.0.0") is False
    assert zc.build_matches("V5.0.0", None) is True  # 无版本号不过滤
    # V5.0 不应误匹配 V5.0.0（token 完整相等判定）
    assert zc.build_matches("V5.0.0", "V5.0") is False
