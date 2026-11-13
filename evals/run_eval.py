# -*- coding: utf-8 -*-
"""V2-8 回归评测集运行器（一条命令跑完金标准，全离线 mock，无副作用）。

用法：
    python -m evals.run_eval          # 全绿退出 0；任何差异退出 1
或作为 pytest 门禁被 tests/unit/test_evals.py 调用（run_all）。

设计口径（对齐需求文档 §9 / B8）：
- 全程 mock + 关闭 trace/feishu/polish/charts/native_fc，不打网络、不落文件、不产生外部调用；
- 期望值来自 cases.json（V1 基线真实产出的拍照冻结），比对逐字段进行；
- 任一字段不符即该 case 失败，整体退出码非 0，作为「V2 功能合入前必须通过」的门禁。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from report_agent.config import load_config
from report_agent.main import build_agent

CASES_PATH = Path(__file__).with_name("cases.json")


def _observe(state: Any) -> Dict[str, Any]:
    """从一轮执行后的 AgentState 抽取评测关心的可观测字段。"""
    obs: Dict[str, Any] = {
        "status": state.status,
        "version_name": state.version_name,
        "no_stats": state.stats_obj is None,
    }
    report = state.report_markdown or ""
    obs["report_contains"] = report  # 供包含式断言（子串命中）使用
    if state.stats_obj is not None:
        s = state.stats_obj.summary
        c = state.stats_obj.conclusion
        obs.update({
            "total": s.total,
            "closed": s.closed,
            "pending_resolve": s.pending_resolve,
            "pending_accept": s.pending_accept,
            "postponed": s.postponed,
            "fix_coverage": s.fix_coverage,
            "p1_pass": c.p1_pass,
            "p2_pass": c.p2_pass,
            "p3_pass": c.p3_pass,
            "release_advice": c.release_advice,
        })
    return obs


def _check_field(key: str, expect_val: Any, obs: Dict[str, Any]) -> Tuple[bool, str]:
    """按 key 语义比对单字段，返回 (是否通过, 观测到的值描述)。"""
    if key == "advice_contains":
        ok = str(expect_val) in (obs.get("release_advice") or "")
        return ok, f"advice={obs.get('release_advice')!r}"
    if key == "report_contains":
        ok = str(expect_val) in (obs.get("report_contains") or "")
        return ok, f"report前40字={obs.get('report_contains', '')[:40]!r}"
    val = obs.get(key)
    return val == expect_val, f"{key}={val!r}"


def run_all() -> List[Dict[str, Any]]:
    """跑完金标准全部用例，返回每例结果明细（供脚本与 pytest 共用）。"""
    spec = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    base = dict(spec["base_overrides"])
    results: List[Dict[str, Any]] = []
    for case in spec["cases"]:
        overrides = {**base, "mock.fault": case.get("fault", "none")}
        cfg = load_config(**overrides)
        agent, _ = build_agent(cfg)
        state = agent.run(case["input"])
        obs = _observe(state)
        diffs: List[str] = []
        for key, expect_val in case["expect"].items():
            ok, detail = _check_field(key, expect_val, obs)
            if not ok:
                diffs.append(f"  ✗ {key}: 期望 {expect_val!r}，{detail}")
        results.append({"id": case["id"], "passed": not diffs, "diffs": diffs})
    return results


def main() -> int:
    results = run_all()
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['id']}")
        for d in r["diffs"]:
            print(d)
    print(f"\n评测集：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
