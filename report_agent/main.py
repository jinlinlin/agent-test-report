"""CLI 入口：输入版本名称 → 运行 Agent → 输出 Markdown 报告 + Trace。

用法：
    python -m report_agent.main "帮我生成 UniResearch V5.0.0 的测试报告"
    python -m report_agent.main "UniResearch V5.0.0" --fault not_found
    python -m report_agent.main "UniResearch V5.0.0" --max-steps 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent.loop import ReportAgent
from .agent.planner import build_planner
from .config import load_config
from .llm.client import LLMClient
from .tools.base import ToolRegistry
from .tools.render_report import RenderReportTool
from .tools.statistics import ComputeStatisticsTool
from .tools.zentao_bugs import GetVersionBugsTool
from .tools.zentao_version import GetVersionInfoTool
from .trace.tracer import Tracer


def build_registry(cfg) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(GetVersionInfoTool(cfg))
    reg.register(GetVersionBugsTool(cfg))
    reg.register(ComputeStatisticsTool())
    reg.register(RenderReportTool())
    return reg


def build_agent(cfg) -> tuple[ReportAgent, Tracer]:
    registry = build_registry(cfg)
    llm = LLMClient(cfg)
    planner = build_planner(cfg, llm, registry.specs())
    tracer = Tracer(enabled=cfg.trace.enabled, trace_dir=cfg.trace_dir)
    return ReportAgent(cfg, registry, planner, tracer), tracer


def _safe_name(version_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version_name or "report"))


def run_once(user_input: str, **overrides) -> tuple[Path | None, Path | None, str]:
    cfg = load_config(**overrides)
    agent, tracer = build_agent(cfg)
    state = agent.run(user_input)

    # 落盘报告
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = cfg.report_dir / f"{_safe_name(state.version_name or 'report')}.md"
    if state.report_markdown:
        report_path.write_text(state.report_markdown, encoding="utf-8")

    # 落盘 Trace
    trace_path = tracer.save(f"{_safe_name(state.version_name or 'report')}.json")

    return report_path, trace_path, state.status


def run_probe(version_name: str = "", **overrides) -> int:
    """禅道接入自检：验证认证/端点是否可用，并打印真实字段名（用于核对 §10 字段映射）。"""
    import json as _json

    cfg = load_config(**{**overrides, "zentao.mode": "live"})
    from .tools.zentao_client import ZentaoError, ZentaoRestClient, _extract_list

    client = ZentaoRestClient(cfg)
    print(f"[probe] base = {cfg.zentao.base_url}{cfg.zentao.api_prefix}")
    try:
        token = client.authenticate()
        print(f"[probe] 认证成功，token 前缀：{str(token)[:8]}...")
    except ZentaoError as exc:
        print(f"[probe][失败] 认证阶段：({exc.kind}) {exc}")
        return 2

    try:
        payload = client.get("/products", params={"limit": 5, "page": 1})
        items = _extract_list(payload, ["products", "data"])
        print(f"[probe] /products 返回 {len(items)} 条（取前 5）")
        if items:
            print("[probe] product 字段名：" + ", ".join(sorted(items[0].keys())))
            print("[probe] product 样例：" + _json.dumps(items[0], ensure_ascii=False)[:400])
    except ZentaoError as exc:
        print(f"[probe][失败] /products：({exc.kind}) {exc}")
        return 2

    # 若指定版本名，进一步拉一条缺陷看字段
    target = version_name or (items[0].get(cfg.zentao.field_product_name) if items else None)
    if target:
        product = client.find_product(str(target)) if version_name else (items[0] if items else None)
        if product:
            pid = product.get("id")
            try:
                bp = client.get(f"/products/{pid}/bugs", params={"limit": 3, "page": 1})
                bugs = _extract_list(bp, ["bugs", "data"])
                print(f"[probe] product[{pid}] /bugs 返回 {len(bugs)} 条（取前 3）")
                if bugs:
                    print("[probe] bug 字段名：" + ", ".join(sorted(bugs[0].keys())))
                    print("[probe] bug 样例：" + _json.dumps(bugs[0], ensure_ascii=False)[:600])
                    print("[probe] → 对照 settings.yaml 的 field_module/field_severity/field_status/"
                          "field_resolution/field_activated 是否与上述字段名一致；不一致则改配置。")
            except ZentaoError as exc:
                print(f"[probe][失败] /bugs：({exc.kind}) {exc}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="测试报告生成 Agent V1.0")
    parser.add_argument("user_input", nargs="?", default="",
                        help="自然语言版本名称，如：帮我生成 UniResearch V5.0.0 的测试报告")
    parser.add_argument("--fault", default=None,
                        help="故障注入：none|not_found|timeout|error|empty_bugs")
    parser.add_argument("--max-steps", type=int, default=None, help="最大执行步数（验证 V1-005 可调小）")
    parser.add_argument("--mode", default=None, help="禅道模式：mock|live")
    parser.add_argument("--probe", action="store_true",
                        help="禅道接入自检：验证认证/端点并打印真实字段名（需先配好 base_url 与账号）")
    args = parser.parse_args(argv)

    overrides = {}
    if args.fault is not None:
        overrides["mock.fault"] = args.fault
    if args.max_steps is not None:
        overrides["agent.max_steps"] = args.max_steps
    if args.mode is not None:
        overrides["zentao.mode"] = args.mode

    if args.probe:
        return run_probe(args.user_input, **overrides)

    if not args.user_input:
        parser.error("缺少版本名称输入（或使用 --probe 自检）")

    report_path, trace_path, status = run_once(args.user_input, **overrides)

    print(f"[状态] {status}")
    if report_path and report_path.exists():
        print(f"[报告] {report_path}")
    if trace_path and trace_path.exists():
        print(f"[Trace] {trace_path}")
    if status == "clarify":
        print("[澄清] 未识别到版本名称，请补充完整版本号后重试（例如：UniResearch V5.0.0）。")
    return 0 if status in ("finished", "clarify") else 1


if __name__ == "__main__":
    sys.exit(main())
