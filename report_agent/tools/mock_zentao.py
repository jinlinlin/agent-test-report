"""Mock 禅道数据源（F6 故障注入 + 离线跑通链路）。

真实接入时把 zentao.mode 改为 live，本模块不参与。
故障开关 fault: none | not_found | timeout | error | empty_bugs。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ToolResult

# 已知版本（精确全名匹配，含项目前缀）；product_name/build 与 live 返回结构对齐（V1 尾账），
# 使离线链路可覆盖飞书多项目落点路由与参考文档检索分支（两者均以该两字段非空为前提）。
MOCK_VERSIONS: Dict[str, Dict[str, Any]] = {
    "UniResearch V5.0.0": {
        "name": "UniResearch V5.0.0",
        "description": "科研管理和其他功能增强；全局 Credits 计费改造；历史版本功能回归。",
        "test_start": "2026-09-30",
        "test_end": "2026-10-23",
        "product_name": "UniResearch",
        "build": "V5.0.0",
        "build_created": "2026-09-25",
    },
    # V2-8 回归评测集补充版本（覆盖发布准入的另两条结论分支，见 evals/）：
    # V6.0.0 全部已关闭 → 「均达标可发布」分支；V6.1.0 存在激活 P1 → 「P1 否决」分支。
    "UniResearch V6.0.0": {
        "name": "UniResearch V6.0.0",
        "description": "评测基准版本（全关闭，用于钉死达标可发布分支）。",
        "test_start": "2026-10-01",
        "test_end": "2026-10-20",
        "product_name": "UniResearch",
        "build": "V6.0.0",
        "build_created": "2026-10-25",
    },
    "UniResearch V6.1.0": {
        "name": "UniResearch V6.1.0",
        "description": "评测基准版本（含激活 P1，用于钉死 P1 否决分支）。",
        "test_start": "2026-10-25",
        "test_end": "2026-11-10",
        "product_name": "UniResearch",
        "build": "V6.1.0",
        "build_created": "2026-11-05",
    },
    # 第二个项目（一表通）：专供 V2-1 “多项目/两缺先问项目不跳项目串候选”分支离线可测。
    "一表通项目 V1.2.0": {
        "name": "一表通项目 V1.2.0",
        "description": "一表通报表导出与汇总（评测基准版本，供候选列表第二项目）。",
        "test_start": "2026-07-20",
        "test_end": "2026-08-05",
        "product_name": "一表通项目",
        "build": "V1.2.0",
        "build_created": "2026-08-10",
    },
    "一表通项目 V1.3.0": {
        "name": "一表通项目 V1.3.0",
        "description": "一表通权限粒度到列级（评测基准版本，供候选列表第二项目最新版）。",
        "test_start": "2026-09-01",
        "test_end": "2026-09-12",
        "product_name": "一表通项目",
        "build": "V1.3.0",
        "build_created": "2026-09-15",
    },
}


def _bug(bid, module, severity, status, resolution, activated=0, opened_by="", resolved_by="",
         title=None, priority=None, btype=None, steps=None, assigned_to=None) -> Dict[str, Any]:
    mod = module.strip("/ ") or "功能"
    return {
        "id": bid,
        "module": module,
        "severity": severity,      # 1=P1 致命, 2=P2 严重, 3=P3 一般, 4=P4 建议
        "status": status,          # active | resolved | closed
        "resolution": resolution,  # fixed | postponed | willnotfix | notrepro | duplicate | bydesign | external | ""
        "activatedCount": activated,
        "openedBy": opened_by,     # 提交人（测试）
        "resolvedBy": resolved_by, # 解决人（开发/运维）
        # 第 6 节缺陷清单展示字段（未显式传入则按模块/编号派生示例值，供离线预览）
        "title": title or f"【{mod}】示例缺陷用例 {bid}",
        "priority": priority if priority is not None else severity,
        "type": btype or "codeerror",
        "steps": steps or f"1. 进入{mod}；2. 执行相关操作；3. 观察到异常（示例重现步骤）。",
        "assignedTo": assigned_to or (resolved_by or "dev_wang"),
    }


# 代表性缺陷样本（覆盖各模块/等级/状态/resolution/激活次数/提交人/解决人分支）
MOCK_BUGS: Dict[str, List[Dict[str, Any]]] = {
    "UniResearch V5.0.0": [
        _bug(1, "/ 科研管理 / 材料生成", 3, "closed", "fixed", 1, "tester_a", "dev_wang"),
        _bug(2, "/ 科研管理 / 材料生成", 3, "closed", "fixed", 0, "tester_b", "dev_wang"),
        _bug(3, "/ 科研管理 / 材料生成", 2, "closed", "fixed", 2, "tester_a", "dev_zhao"),
        _bug(4, "/ 科研管理 / 材料生成", 4, "resolved", "postponed", 0, "tester_c", ""),
        _bug(5, "/ 科研管理 / 成果管理", 3, "closed", "fixed", 0, "tester_b", "dev_zhao"),
        _bug(6, "/ 科研管理 / 成果管理", 2, "closed", "fixed", 1, "tester_a", "dev_wang"),
        _bug(7, "/ 科研管理 / 课题管理", 3, "resolved", "fixed", 0, "tester_c", "dev_li"),
        _bug(8, "/ 科研管理 / 课题管理", 1, "closed", "fixed", 3, "tester_a", "dev_li"),
        _bug(9, "/ 科研实验", 2, "active", "", 1, "tester_b", ""),
        _bug(10, "/ 科研实验", 3, "active", "", 0, "tester_c", ""),
        _bug(11, "/ 科研实验", 3, "resolved", "fixed", 2, "tester_a", "dev_wang"),
        _bug(12, "/ 科研实验", 4, "closed", "willnotfix", 0, "tester_b", "dev_zhao"),
        _bug(13, "/ 研究助手", 3, "active", "", 0, "tester_c", ""),
        _bug(14, "/ 研究助手", 2, "closed", "fixed", 1, "tester_a", "dev_li"),
        _bug(15, "/ 研究助手", 3, "closed", "fixed", 0, "tester_b", "dev_wang"),
        _bug(16, "/ 文献管理", 3, "closed", "fixed", 0, "tester_c", "dev_zhao"),
        _bug(17, "/ 文献管理", 4, "resolved", "fixed", 0, "tester_a", "dev_li"),
        _bug(18, "/ 登录注册", 2, "active", "", 0, "tester_b", ""),
        _bug(19, "/ 登录注册", 3, "closed", "fixed", 1, "tester_c", "dev_wang"),
        _bug(20, "/ 知识库", 3, "closed", "fixed", 0, "tester_a", "dev_zhao"),
        _bug(21, "/ 知识库", 1, "closed", "fixed", 0, "tester_b", "dev_li"),
        _bug(22, "/ 任务管理", 3, "closed", "fixed", 0, "tester_c", "dev_wang"),
        _bug(23, "/ 任务管理", 4, "resolved", "postponed", 0, "tester_a", ""),
        _bug(24, "/ 公共模块", 3, "closed", "duplicate", 0, "tester_b", "dev_zhao"),
    ],
    # V6.0.0：全部已关闭（fixed/willnotfix/duplicate 均算已关闭），无任何激活 → 达标可发布分支
    "UniResearch V6.0.0": [
        _bug(1, "/ 科研管理 / 课题管理", 1, "closed", "fixed", 0, "tester_a", "dev_li"),
        _bug(2, "/ 科研管理 / 成果管理", 2, "closed", "fixed", 1, "tester_b", "dev_wang"),
        _bug(3, "/ 文献管理", 3, "closed", "fixed", 0, "tester_c", "dev_zhao"),
        _bug(4, "/ 知识库", 4, "closed", "willnotfix", 0, "tester_a", "dev_li"),
        _bug(5, "/ 任务管理", 3, "closed", "duplicate", 0, "tester_b", "dev_wang"),
    ],
    # V6.1.0：含 1 条激活 P1（status=active severity=1）→ p1_pass=False，P1 直接否决分支
    "UniResearch V6.1.0": [
        _bug(1, "/ 登录注册", 1, "active", "", 2, "tester_a", ""),
        _bug(2, "/ 科研实验", 2, "closed", "fixed", 0, "tester_b", "dev_wang"),
        _bug(3, "/ 文献管理", 3, "resolved", "fixed", 0, "tester_c", "dev_li"),
    ],
    # 一表通项目版本：少量代表性缺陷，仅供 V2-1 端到端“选定候选后正常出报告”演示。
    "一表通项目 V1.2.0": [
        _bug(1, "/ 报表导出", 2, "closed", "fixed", 0, "tester_a", "dev_wang"),
        _bug(2, "/ 报表导出", 3, "closed", "fixed", 1, "tester_b", "dev_zhao"),
        _bug(3, "/ 数据汇总", 3, "closed", "fixed", 0, "tester_c", "dev_li"),
    ],
    "一表通项目 V1.3.0": [
        _bug(1, "/ 权限管理", 2, "active", "", 1, "tester_a", ""),
        _bug(2, "/ 权限管理", 3, "closed", "fixed", 0, "tester_b", "dev_wang"),
        _bug(3, "/ 数据汇总", 3, "resolved", "fixed", 0, "tester_c", "dev_li"),
    ],
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


# ---- V2-1 澄清候选查询（与 live 的 list_version_candidates 工具同形）----

def list_products() -> List[str]:
    """已知项目名（去重，长名优先），供意图分类与候选列举。"""
    names = {v.get("product_name", "") for v in MOCK_VERSIONS.values() if v.get("product_name")}
    return sorted(names, key=len, reverse=True)


def _match_product(hint: str) -> str:
    """先精确后包含匹配项目名（对齐 find_product 口径）；无命中返回空串。"""
    h = (hint or "").strip().lower()
    if not h:
        return ""
    names = list_products()  # 已按长度降序，长名（最具体）优先
    for n in names:
        if n.lower() == h:
            return n
    for n in names:
        if n.lower() in h or h in n.lower():
            return n
    return ""


def list_candidates(product_hint: str) -> ToolResult:
    """列该项目全部候选版本，按 build_created 倒序（最新在前）。

    “最新”口径由本函数排序钉死（程序决定），不交模型裁量；未知项目返回空候选（ok 仍为 True，由上层决定转“不存在”还是“先问项目”）。
    """
    prod = _match_product(product_hint)
    if not prod:
        return ToolResult(ok=True, data={"product": "", "candidates": []})
    rows = [v for v in MOCK_VERSIONS.values() if v.get("product_name") == prod]
    rows.sort(key=lambda v: str(v.get("build_created") or ""), reverse=True)
    cands = [
        {"name": v["name"], "build": v.get("build", ""), "created": v.get("build_created", "")}
        for v in rows
    ]
    return ToolResult(ok=True, data={"product": prod, "candidates": cands})
