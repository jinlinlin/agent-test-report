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
from .tools.polish_conclusion import PolishConclusionTool
from .tools.push_feishu import PushFeishuTool
from .tools.render_report import RenderReportTool
from .tools.statistics import ComputeStatisticsTool
from .tools.version_candidates import ListVersionCandidatesTool
from .tools.zentao_bugs import GetVersionBugsTool
from .tools.zentao_version import GetVersionInfoTool
from .trace.tracer import Tracer


def build_registry(cfg, llm=None) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(GetVersionInfoTool(cfg))
    reg.register(GetVersionBugsTool(cfg))
    reg.register(ComputeStatisticsTool())
    # V2-1：只读候选列举工具（供模糊输入澄清时模型/循环调用）；不依赖开关，
    # 仅在 interactive_clarify 开启时才会被 loop 触发，否则链路行为与现状一致。
    reg.register(ListVersionCandidatesTool(cfg))
    # F17：仅开关开启时注册润色工具，关闭时工具集与链路行为与现状完全一致
    if cfg.report.polish_conclusion:
        reg.register(PolishConclusionTool(cfg, llm))
    reg.register(RenderReportTool(cfg))  # cfg 用于 charts 开关与 assets 落盘目录
    reg.register(PushFeishuTool(cfg))
    return reg


def build_agent(cfg, ask=None) -> tuple[ReportAgent, Tracer]:
    llm = LLMClient(cfg)
    registry = build_registry(cfg, llm)
    planner = build_planner(cfg, llm, registry.specs())
    tracer = Tracer(enabled=cfg.trace.enabled, trace_dir=cfg.trace_dir)
    # llm 同时透传给 loop：仅供 V2-1 延伸的 LLM 兜底翻译（llm_intent_fallback 开启才使用），默认关不影响现有链路
    return ReportAgent(cfg, registry, planner, tracer, ask=ask, llm=llm), tracer


def _safe_name(version_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version_name or "report"))


def _terminal_ask(question: str, options) -> str:
    """命令行交互澄清：打印候选并读一行输入。仅在 interactive_clarify 开启且输入歧义时被 loop 调用；
    非交互环境（管道/EOF）返回空串，由 loop 优雅退回 clarify，不会挂起。"""
    print(question)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    try:
        return input("请输入序号或内容（直接回车跳过）：").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def run_once(user_input: str, **overrides) -> tuple[Path | None, Path | None, str, str | None]:
    cfg = load_config(**overrides)
    # 开关开启时才接上终端 input()（V2-1 歧义澄清 或 V2-3 首次询问参考文档链接）；
    # 两者均关时传 None，行为与 V1 逐字一致。
    need_ask = cfg.agent.interactive_clarify or getattr(cfg.memory, "doc_links_enabled", False)
    agent, tracer = build_agent(cfg, ask=(_terminal_ask if need_ask else None))
    state = agent.run(user_input)

    # 落盘报告
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = cfg.report_dir / f"{_safe_name(state.version_name or 'report')}_测试报告.md"
    if state.report_markdown:
        report_path.write_text(state.report_markdown, encoding="utf-8")

    # 落盘 Trace
    trace_path = tracer.save(f"{_safe_name(state.version_name or 'report')}.json")

    return report_path, trace_path, state.status, state.feishu_doc_url


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
            from .tools.zentao_client import person_name
            print("[probe] product 字段名：" + ", ".join(sorted(items[0].keys())))
            print("[probe] product 样例：" + _json.dumps(items[0], ensure_ascii=False)[:400])
            # 高亮“疑似负责人字段”：值为对象(含 realname/account)或名字含 owner/PO/PM/RD 的键
            cand = []
            for k, v in items[0].items():
                looks = isinstance(v, dict) and ("realname" in v or "account" in v)
                named = any(t in k.lower() for t in ("owner", "po", "pm", "rd", "manager"))
                if looks or named:
                    cand.append(f"{k}={person_name(v) or _json.dumps(v, ensure_ascii=False)[:60]}")
            print("[probe] 疑似负责人字段（供设置 field_product_owner）：" + ("; ".join(cand) or "无"))
            print("[probe] → 若其中某项就是“项目负责人”，把键名填到 settings.yaml 的 zentao.field_product_owner（或 .env 的 ZENTAO_FIELD_PRODUCT_OWNER）。")
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
    parser.add_argument("--push-feishu", action="store_true",
                        help="生成后将报告推送至飞书云文档/知识库（需先在 .env 配好飞书凭据与 token）")
    parser.add_argument("--polish", action="store_true",
                        help="开启 F17 结论段 LLM 润色（覆盖 report.polish_conclusion；数字不一致自动回退模板）")
    parser.add_argument("--interactive-clarify", action="store_true", dest="interactive_clarify",
                        help="开启 V2-1 模糊输入交互澄清（覆盖 agent.interactive_clarify；歧义时列候选并终端反问）")
    parser.add_argument("--llm-intent-fallback", action="store_true", dest="llm_intent_fallback",
                        help="开启 V2-1 延伸 LLM 兜底意图翻译（覆盖 agent.llm_intent_fallback；仅在候选集内选择，需可用 LLM）")
    parser.add_argument("--doc-memory", action="store_true", dest="doc_memory",
                        help="开启 V2-3 项目级参考文档链接记忆（覆盖 memory.doc_links_enabled；手册/UI/原型 首次询问后按项目登记、后续同项目复用）")
    parser.add_argument("--scope-grounding", action="store_true", dest="scope_grounding",
                        help="开启 V2-4 测试范围接地（覆盖 scope.enabled；从本版本目录下的测试用例 xlsx 自动提取 2.1 测试范围，提不出则回退占位）")
    parser.add_argument("--test-env", action="store_true", dest="test_env",
                        help="开启 2.2 测试环境接地（覆盖 test_env.enabled；从知识库项目层环境文档逐字提取地址/账号，未命中则回退占位）")
    parser.add_argument("--doc-index", action="store_true", dest="doc_index",
                        help="开启 §1.3 手册/UI/原型链接接地（覆盖 doc_index.enabled；从知识库项目层链接汇总文档逐字提取外链，未命中则维持记忆/人工占位）")
    args = parser.parse_args(argv)

    overrides = {}
    if args.fault is not None:
        overrides["mock.fault"] = args.fault
    if args.max_steps is not None:
        overrides["agent.max_steps"] = args.max_steps
    if args.mode is not None:
        overrides["zentao.mode"] = args.mode
    if args.push_feishu:
        overrides["feishu.enabled"] = True
    if args.polish:
        overrides["report.polish_conclusion"] = True
    if args.interactive_clarify:
        overrides["agent.interactive_clarify"] = True
    if args.llm_intent_fallback:
        overrides["agent.llm_intent_fallback"] = True
    if args.doc_memory:
        overrides["memory.doc_links_enabled"] = True
    if args.scope_grounding:
        overrides["scope.enabled"] = True
    if args.test_env:
        overrides["test_env.enabled"] = True
    if args.doc_index:
        overrides["doc_index.enabled"] = True

    if args.probe:
        return run_probe(args.user_input, **overrides)

    if not args.user_input:
        parser.error("缺少版本名称输入（或使用 --probe 自检）")

    report_path, trace_path, status, feishu_url = run_once(args.user_input, **overrides)

    print(f"[状态] {status}")
    if report_path and report_path.exists():
        print(f"[报告] {report_path}")
    if trace_path and trace_path.exists():
        print(f"[Trace] {trace_path}")
    if feishu_url:
        print(f"[飞书] {feishu_url}")
    if status == "clarify":
        print("[澄清] 未识别到版本名称，请补充完整版本号后重试（例如：UniResearch V5.0.0）。")
    return 0 if status in ("finished", "clarify") else 1


if __name__ == "__main__":
    sys.exit(main())
