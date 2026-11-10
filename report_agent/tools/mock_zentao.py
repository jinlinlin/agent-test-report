"""Mock 禅道数据源（F6 故障注入 + 离线跑通链路）。

真实接入时把 zentao.mode 改为 live，本模块不参与。
故障开关 fault: none | not_found | timeout | error | empty_bugs。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ToolResult

# 已知版本（精确全名匹配，含项目前缀）
MOCK_VERSIONS: Dict[str, Dict[str, Any]] = {
    "UniResearch V5.0.0": {
        "name": "UniResearch V5.0.0",
        "description": "科研管理和其他功能增强；全局 Credits 计费改造；历史版本功能回归。",
        "test_start": "2026-09-30",
        "test_end": "2026-10-23",
    }
}


def _bug(bid, module, severity, status, resolution, activated=0) -> Dict[str, Any]:
    return {
        "id": bid,
        "module": module,
        "severity": severity,      # 1=P1 致命, 2=P2 严重, 3=P3 一般, 4=P4 建议
        "status": status,          # active | resolved | closed
        "resolution": resolution,  # fixed | postponed | willnotfix | notrepro | duplicate | bydesign | external | ""
        "activatedCount": activated,
    }


# 代表性缺陷样本（覆盖各模块/等级/状态/resolution/激活次数分支）
MOCK_BUGS: Dict[str, List[Dict[str, Any]]] = {
    "UniResearch V5.0.0": [
        _bug(1, "/ 科研管理 / 材料生成", 3, "closed", "fixed", 1),
        _bug(2, "/ 科研管理 / 材料生成", 3, "closed", "fixed", 0),
        _bug(3, "/ 科研管理 / 材料生成", 2, "closed", "fixed", 2),
        _bug(4, "/ 科研管理 / 材料生成", 4, "resolved", "postponed", 0),
        _bug(5, "/ 科研管理 / 成果管理", 3, "closed", "fixed", 0),
        _bug(6, "/ 科研管理 / 成果管理", 2, "closed", "fixed", 1),
        _bug(7, "/ 科研管理 / 课题管理", 3, "resolved", "fixed", 0),
        _bug(8, "/ 科研管理 / 课题管理", 1, "closed", "fixed", 3),
        _bug(9, "/ 科研实验", 2, "active", "", 1),
        _bug(10, "/ 科研实验", 3, "active", "", 0),
        _bug(11, "/ 科研实验", 3, "resolved", "fixed", 2),
        _bug(12, "/ 科研实验", 4, "closed", "willnotfix", 0),
        _bug(13, "/ 研究助手", 3, "active", "", 0),
        _bug(14, "/ 研究助手", 2, "closed", "fixed", 1),
        _bug(15, "/ 研究助手", 3, "closed", "fixed", 0),
        _bug(16, "/ 文献管理", 3, "closed", "fixed", 0),
        _bug(17, "/ 文献管理", 4, "resolved", "fixed", 0),
        _bug(18, "/ 登录注册", 2, "active", "", 0),
        _bug(19, "/ 登录注册", 3, "closed", "fixed", 1),
        _bug(20, "/ 知识库", 3, "closed", "fixed", 0),
        _bug(21, "/ 知识库", 1, "closed", "fixed", 0),
        _bug(22, "/ 任务管理", 3, "closed", "fixed", 0),
        _bug(23, "/ 任务管理", 4, "resolved", "postponed", 0),
        _bug(24, "/ 公共模块", 3, "closed", "duplicate", 0),
    ]
}


def _match_version(query: str) -> str | None:
    """精确全名匹配；容忍用户输入包裹在自然语言句子中。"""
    q = (query or "").strip()
    for name in MOCK_VERSIONS:
        if name == q or name in q:
            return name
    return None


def fetch_version(version_name: str, fault: str = "none") -> ToolResult:
    if fault == "timeout":
        return ToolResult(ok=False, error="禅道接口超时", meta={"fetch_status": "timeout"})
    if fault == "error":
        return ToolResult(ok=False, error="禅道接口返回错误(500)", meta={"fetch_status": "error"})
    if fault == "not_found":
        return ToolResult(ok=False, error="版本不存在", meta={"fetch_status": "not_found"})

    key = _match_version(version_name)
    if key is None:
        return ToolResult(ok=False, error="版本不存在", meta={"fetch_status": "not_found"})
    return ToolResult(ok=True, data=dict(MOCK_VERSIONS[key], fetch_status="ok"))


def fetch_bugs(version_name: str, fault: str = "none") -> ToolResult:
    if fault == "timeout":
        return ToolResult(ok=False, error="禅道接口超时", meta={"fetch_status": "timeout"})
    if fault == "error":
        return ToolResult(ok=False, error="禅道接口返回错误(500)", meta={"fetch_status": "error"})
    if fault == "empty_bugs":
        return ToolResult(ok=True, data=[], meta={"fetch_status": "ok"})

    key = _match_version(version_name)
    if key is None:
        return ToolResult(ok=False, error="版本不存在", meta={"fetch_status": "not_found"})
    return ToolResult(ok=True, data=[dict(b) for b in MOCK_BUGS.get(key, [])], meta={"fetch_status": "ok"})
