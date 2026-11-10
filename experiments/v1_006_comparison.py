"""V1-006 对照实验脚手架（需求文档 §8.1 V1-006 / 交付物 #7）。

目的（学习用，不修复）：证明「取消独立统计工具、改由模型自行汇总」会引入数字偏差，
从而反证 F3 独立统计工具的必要性。

做法：
- 同批数据：固定 mock 缺陷集（`mock_zentao.MOCK_BUGS`），保证可复现、不依赖线上禅道。
- 基线：`compute_statistics` 纯函数产出的结构化指标（ground truth）。
- 实验组：把原始缺陷 JSON 交给 LLM，要求它「不用任何工具、自行逐项汇总」，
  同模型、固定 temperature=0 连续跑 N 次（默认 3）。
- 逐项 diff：任一数字字段与基线不一致，即记为一条反例。

硬性约束：无可用 LLM Key 时，本脚本**明确拒绝产出**，绝不伪造模型输出
（伪造「模型汇总结果」本身就违背本项目防幻觉原则）。

用法：
    python -m experiments.v1_006_comparison
    python -m experiments.v1_006_comparison --runs 3 --out experiments/v1_006_反例记录.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 保证以脚本直跑时也能导入 report_agent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_agent.config import load_config  # noqa: E402
from report_agent.llm.client import LLMClient  # noqa: E402
from report_agent.tools import mock_zentao  # noqa: E402
from report_agent.tools.statistics import compute_statistics  # noqa: E402

TARGET_VERSION = "UniResearch V5.0.0"

# 只比对整数计数字段（数字最硬，模型最容易汇总出错）；比率字段依赖总数口径，附列参考
CMP_FIELDS = ["total", "closed", "pending_resolve", "pending_accept", "postponed", "activated_ge1_count"]

BUCKET_RULES = (
    "四类互斥分桶（判定顺序，已关闭优先）：\n"
    "1) 已关闭 closed：status=closed（无论 resolution 为何）；\n"
    "2) 待解决 pending_resolve：status=active；\n"
    "3) 延期 postponed：status≠closed 且 resolution=postponed；\n"
    "4) 待验收 pending_accept：status=resolved 且 resolution≠postponed。\n"
    "activated_ge1_count = activatedCount≥1 的缺陷条数。"
)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if "{" not in t or "}" not in t:
        return None
    t = t[t.index("{"): t.rindex("}") + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def build_prompt(bugs: List[Dict[str, Any]]) -> str:
    return (
        "你将直接对一批缺陷做汇总统计，**不调用任何工具，只靠你自己数**。\n"
        f"分桶规则：\n{BUCKET_RULES}\n"
        "缺陷明细（JSON 数组）：\n"
        f"{json.dumps(bugs, ensure_ascii=False)}\n"
        "请只输出一个 JSON 对象，键为："
        + ", ".join(CMP_FIELDS)
        + "，值为对应整数计数。不要输出任何多余文字。"
    )


def diff_run(ground: Dict[str, Any], model: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if model is None:
        return [{"field": "<parse>", "tool": "<json>", "model": "解析失败"}]
    mismatches = []
    for f in CMP_FIELDS:
        gv, mv = ground.get(f), model.get(f)
        try:
            mv_i = int(mv) if mv is not None else None
        except (TypeError, ValueError):
            mv_i = None
        if gv != mv_i:
            mismatches.append({"field": f, "tool": gv, "model": mv_i})
    return mismatches


def run(runs: int, out_path: Path) -> int:
    cfg = load_config()
    llm = LLMClient(cfg)
    if not llm.available:
        msg = (
            "# V1-006 对照实验：未运行\n\n"
            "本实验需要真实 LLM 产出『模型自行汇总』的结果用于与独立统计工具 diff。\n"
            "当前未检测到 OPENAI_API_KEY（`LLMClient.available=False`），依据防幻觉原则**拒绝伪造模型输出**，"
            "故不产出反例记录。\n\n"
            "运行前置：配置 `OPENAI_API_KEY`（可选覆盖 `llm.base_url` / `llm.model`，temperature 固定 0），再执行：\n"
            "```\npython -m experiments.v1_006_comparison --runs 3\n```"
        )
        print(msg)
        print("\n[提示] 未写入结果文件：没有真实的模型汇总数据，不落任何伪造记录。")
        return 3

    # 同批数据
    version_info = mock_zentao.fetch_version(TARGET_VERSION).data
    bugs = mock_zentao.fetch_bugs(TARGET_VERSION).data
    ground_obj = compute_statistics(version_info, bugs)
    ground = ground_obj.summary.to_dict() if hasattr(ground_obj.summary, "to_dict") else vars(ground_obj.summary)

    # 模型自行汇总，固定 temperature=0，跑 N 次
    cfg.llm.temperature = 0.0
    prompt = build_prompt(bugs)
    system = "你是精确的计数助手，严格按给定规则统计并只输出 JSON。"

    run_reports: List[List[Dict[str, Any]]] = []
    for i in range(runs):
        raw = llm.complete(prompt, system=system)
        model = _extract_json(raw)
        run_reports.append(diff_run(ground, model))

    total_mismatch = sum(len(r) for r in run_reports)
    lines: List[str] = []
    lines.append("# V1-006 对照实验反例记录\n")
    lines.append(f"- 数据批次：mock `{TARGET_VERSION}`（{len(bugs)} 条缺陷，固定可复现）")
    lines.append(f"- 模型：{cfg.llm.model}  base_url：{cfg.llm.base_url}  temperature：0")
    lines.append(f"- 运行次数：{runs}  独立统计基线：{ {k: ground[k] for k in CMP_FIELDS} }")
    lines.append(f"- 偏差字段总数：{total_mismatch}\n")
    lines.append("> 基线为独立统计工具 `compute_statistics` 的实算值；『模型』列为 LLM 自行汇总结果。任一不一致即为反例。\n")
    for idx, mm in enumerate(run_reports, 1):
        lines.append(f"## 第 {idx} 次")
        if not mm:
            lines.append("- 与基线完全一致（本次未产生偏差）")
        else:
            for d in mm:
                lines.append(f"- ❌ `{d['field']}`：基线={d['tool']} 模型={d['model']}")
        lines.append("")
    lines.append("## 结论")
    if total_mismatch == 0:
        lines.append("- 本次模型汇总与工具基线一致；这**不推翻**独立统计的必要性——"
                     "模型汇总在更大/更复杂数据集与不同温度下极易漂移，独立工具的价值在于**确定性可复现**。")
    else:
        lines.append(f"- 观察到 {total_mismatch} 处偏差，反证『取消独立统计、交模型汇总』会引入不可控数字错误，"
                     "必须坚持 F3 独立统计工具。")

    content = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(content)
    print(f"\n[已写入反例记录] {out_path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V1-006 对照实验：模型自行汇总 vs 独立统计工具")
    ap.add_argument("--runs", type=int, default=3, help="同一批数据重复运行次数（默认 3）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "v1_006_反例记录.md"),
                    help="反例记录输出路径")
    args = ap.parse_args(argv)
    return run(args.runs, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
