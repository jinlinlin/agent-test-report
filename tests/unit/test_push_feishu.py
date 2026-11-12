"""push_feishu 工具与飞书门控的单元测试（不联网）。

覆盖：
- 工具前置校验分支（未启用/缺凭据/缺 folder/缺 wiki 配置）均如实返回 ok=False；
- 循环门控：推送成功写 feishu_doc_url；推送失败为非致命，报告仍 finished、不推翻为 failed。
"""
from report_agent.agent.loop import ReportAgent
from report_agent.agent.planner import build_planner
from report_agent.config import load_config
from report_agent.llm.client import LLMClient
from report_agent.main import build_registry
from report_agent.tools.base import Tool, ToolResult
from report_agent.tools.push_feishu import PushFeishuTool
from report_agent.tools.feishu_client import FeishuClient
from report_agent.trace.tracer import Tracer

BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False}


def _feishu_cfg(**over):
    return load_config(**{**BASE, "feishu.enabled": True, **over})


def test_disabled_returns_error():
    cfg = load_config(**{**BASE, "feishu.enabled": False})
    res = PushFeishuTool(cfg).run(report_markdown="# x", version_name="UniResearch V5.0.0")
    assert res.ok is False and "未启用" in res.error


def test_missing_credential():
    cfg = _feishu_cfg(**{"feishu.app_id": "", "feishu.app_secret": ""})
    res = PushFeishuTool(cfg).run(report_markdown="# x")
    assert res.ok is False and "FEISHU_APP_ID" in res.error


def test_missing_folder_token():
    cfg = _feishu_cfg(**{"feishu.app_id": "cli_x", "feishu.app_secret": "s", "feishu.upload_folder_token": ""})
    res = PushFeishuTool(cfg).run(report_markdown="# x")
    assert res.ok is False and "FEISHU_FOLDER_TOKEN" in res.error


def test_wiki_missing_space_or_node():
    cfg = _feishu_cfg(**{"feishu.app_id": "cli_x", "feishu.app_secret": "s",
                         "feishu.upload_folder_token": "fld", "feishu.target": "wiki",
                         "feishu.wiki_space_id": "", "feishu.wiki_parent_node": ""})
    res = PushFeishuTool(cfg).run(report_markdown="# x")
    assert res.ok is False and "FEISHU_WIKI_SPACE_ID" in res.error


class _StubPush(Tool):
    name = "push_feishu"
    description = "stub"
    parameters = {}

    def __init__(self, ok: bool, payload: str) -> None:
        self._ok, self._payload = ok, payload

    def run(self, **kwargs) -> ToolResult:
        if self._ok:
            return ToolResult(ok=True, data=self._payload)
        return ToolResult(ok=False, error=self._payload)


def _run_with_stub(ok: bool, payload: str):
    cfg = _feishu_cfg()
    registry = build_registry(cfg)
    registry.register(_StubPush(ok, payload))  # 覆盖真实推送工具，避免联网
    planner = build_planner(cfg, LLMClient(cfg), registry.specs())
    agent = ReportAgent(cfg, registry, planner, Tracer(enabled=False))
    return agent.run("帮我生成 UniResearch V5.0.0 的测试报告")


def test_loop_push_success_sets_url():
    state = _run_with_stub(True, "https://my.feishu.cn/wiki/NODE123")
    assert state.status == "finished"
    assert state.feishu_doc_url == "https://my.feishu.cn/wiki/NODE123"
    assert state.report_markdown is not None


def test_loop_push_failure_is_nonfatal():
    state = _run_with_stub(False, "模拟推送失败")
    # 推送失败不推翻已生成报告：仍 finished，报告在，错误被记录
    assert state.status == "finished"
    assert state.report_markdown is not None
    assert state.feishu_doc_url is None
    assert "模拟推送失败" in state.feishu_error


class _WikiCfg:
    def __init__(self, projects_parent="", parent=""):
        self.wiki_space_id = "sp"
        self.wiki_projects_parent = projects_parent
        self.wiki_parent_node = parent


class _RouteClient:
    def __init__(self, mapping):
        self._m = mapping
        self.called = False

    def find_child_by_title(self, space, parent, name):
        self.called = True
        return self._m.get(name, "")


def test_resolve_parent_routes_by_product():
    fc = _RouteClient({"UniResearch": "NODE_UNI"})
    got = PushFeishuTool._resolve_parent(fc, _WikiCfg("PROJ_PARENT", "NODE_DEFAULT"), "UniResearch")
    assert got == "NODE_UNI" and fc.called


def test_resolve_parent_falls_back_when_no_hit():
    fc = _RouteClient({})
    got = PushFeishuTool._resolve_parent(fc, _WikiCfg("PROJ_PARENT", "NODE_DEFAULT"), "未知产品")
    assert got == "NODE_DEFAULT"


def test_resolve_parent_without_projects_parent():
    fc = _RouteClient({})
    got = PushFeishuTool._resolve_parent(fc, _WikiCfg("", "NODE_DEFAULT"), "UniResearch")
    assert got == "NODE_DEFAULT" and not fc.called


class _NodesClient(FeishuClient):
    def __init__(self, nodes):
        self._nodes = nodes

    def list_nodes(self, space_id, parent_node_token=""):
        return self._nodes


def test_find_child_by_title_matches():
    c = _NodesClient([
        {"title": "Uniresearch项目测试", "node_token": "N_UNI"},
        {"title": "一表通项目测试", "node_token": "N_YBT"},
    ])
    assert c.find_child_by_title("sp", "parent", "UniResearch") == "N_UNI"
    assert c.find_child_by_title("sp", "parent", "一表通项目") == "N_YBT"
    assert c.find_child_by_title("sp", "parent", "不存在产品") == ""
    assert c.find_child_by_title("sp", "parent", "") == ""


def test_find_child_node_exact():
    """版本目录查找：按标题全等命中，部分匹配不算。"""
    c = _NodesClient([
        {"title": "V5.0.0", "node_token": "N500"},
        {"title": "V5.1.0", "node_token": "N510"},
    ])
    assert c.find_child_node("sp", "p", "V5.0.0") == "N500"
    assert c.find_child_node("sp", "p", "V5.0") == ""
    assert c.find_child_node("sp", "p", "") == ""


class _VersionClient:
    def __init__(self, existing="", create_token="NEW", create_raises=False):
        self._existing = existing
        self._create_token = create_token
        self._create_raises = create_raises
        self.created = False

    def find_child_node(self, space, parent, title):
        return self._existing

    def create_node(self, space, parent, title):
        self.created = True
        if self._create_raises:
            from report_agent.tools.feishu_client import FeishuError
            raise FeishuError("权限不足", "error")
        return self._create_token


def test_resolve_version_node_reuses_existing():
    fc = _VersionClient(existing="V_NODE")
    got = PushFeishuTool._resolve_version_node(fc, _WikiCfg(), "PROJ", "V5.0.0")
    assert got == "V_NODE" and not fc.created


def test_resolve_version_node_creates_when_missing():
    fc = _VersionClient(existing="", create_token="NEW_NODE")
    got = PushFeishuTool._resolve_version_node(fc, _WikiCfg(), "PROJ", "V5.0.0")
    assert got == "NEW_NODE" and fc.created


def test_resolve_version_node_no_build_falls_back():
    fc = _VersionClient()
    got = PushFeishuTool._resolve_version_node(fc, _WikiCfg(), "PROJ", "")
    assert got == "PROJ" and not fc.created


def test_resolve_version_node_create_failure_falls_back():
    fc = _VersionClient(existing="", create_raises=True)
    got = PushFeishuTool._resolve_version_node(fc, _WikiCfg(), "PROJ", "V5.0.0")
    assert got == "PROJ"
