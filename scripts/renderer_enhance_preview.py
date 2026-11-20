"""渲染增强效果预览（一次性脚本，不改动 report_agent 生产代码）：

在当前 V5.2.0 报告产物基础上，演示 4 处 renderer 增强的实际效果——
  1) H1 标题行：md 本地产物首行补「# {产品} {版本} 测试报告」（飞书推送侧仍可抑制）；
  2) 4.1.1「/」模块归并显示名：禅道未配置模块的缺陷在表内显示为「未分配模块」；
  3) 4.3/4.4 说明行：图前补一行按 openedBy/resolvedBy 聚合的说明（名字与件数取真实统计）；
  4) §6 禅道链接：引导语后附「按版本筛选查阅」的禅道缺陷列表链接（base 取 .env 的 ZENTAO_BASE_URL）。

取数走真实链路（live 只读），产物写到 output/preview_V5.2.0_渲染增强预览.md，原报告不动。
用法：python scripts/renderer_enhance_preview.py "UniResearch V5.2.0"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_agent.config import load_config
from report_agent.tools.base import ToolRegistry
from report_agent.tools.statistics import ComputeStatisticsTool
from report_agent.tools.zentao_bugs import GetVersionBugsTool
from report_agent.tools.zentao_version import GetVersionInfoTool

VERSION_INPUT = sys.argv[1] if len(sys.argv) > 1 else "UniResearch V5.2.0"


def _persons_line(items, verb: str) -> str:
    """（备用，当前未接入）说明行文案：增强 3 已被拍板否决，保留函数不再调用。"""
    if not items:
        return ""
    total = sum(p.total for p in items)
    top = items[0]
    names = "、".join(f"{p.name}({p.total})" for p in items)
    return (
        f"本版本共 {len(items)} 人{verb}，合计 {total} 条；"
        f"{verb}最多的是 {top.name}（{top.total} 条，占 {round(top.total / total * 100, 1)}%）。"
        f"每人明细：{names}。"
    )


def main() -> int:
    cfg = load_config(**{"zentao.mode": "live", "feishu.enabled": False})
    reg = ToolRegistry()
    reg.register(GetVersionInfoTool(cfg))
    reg.register(GetVersionBugsTool(cfg))
    reg.register(ComputeStatisticsTool())

    cache = Path("output/preview_stats_cache.json")
    if cache.exists():  # 命中缓存则零联网重跑（首次取数后复用，避免每改一处就再拉 136 条）
        blob = __import__("json").loads(cache.read_text(encoding="utf-8"))
        vi, bugs, closed_other = blob["vi"], blob["bugs"], blob["closed_other"]
    else:
        vi_res = reg.get("get_version_info").run(version_name=VERSION_INPUT, user_input=VERSION_INPUT)
        if not vi_res.ok:
            print(f"[失败] 版本信息获取：{vi_res.error}")
            return 1
        vi = vi_res.data or {}
        bugs_res = reg.get("get_version_bugs").run(
            version_name=VERSION_INPUT, version_info=vi, user_input=VERSION_INPUT)
        if not bugs_res.ok:
            print(f"[失败] 缺陷明细获取：{bugs_res.error}")
            return 1
        bugs, closed_other = bugs_res.data, bugs_res.meta.get("closed_other_bugs")
        import json
        cache.write_text(json.dumps({"vi": vi, "bugs": bugs, "closed_other": closed_other},
                                    ensure_ascii=False), encoding="utf-8")
    stats_res = reg.get("compute_statistics").run(
        version_info=vi, bugs=bugs, closed_other_bugs=closed_other)
    stats = stats_res.meta["stats_obj"]

    src = Path("output/UniResearch_V5.2.0_测试报告.md")
    md = src.read_text(encoding="utf-8")

    # ── 增强 1：H1 标题行（不能拿 startswith("# ") 判存在——章节标题也是 "# " 开头）──
    title = f"# {vi.get('product_name', '')} {vi.get('build', '')} 测试报告".strip()
    first = md.lstrip().splitlines()[0] if md.strip() else ""
    if not (first.startswith("# ") and first.endswith("测试报告")):
        md = f"{title}\n\n{md}"

    # ── 增强 2（已否决）：「/」模块行保持原样不改名（用户 2026-11-20 二次拍板：不变，保持 /）──

    # ── 增强 3（已否决）：4.3/4.4 保持原样裸图，不加图前说明行（用户 2026-11-20 拍板）──

    # ── 增强 4：§6 禅道链接（分页列表经典路由 bug-browse-{productID}-0-all）──
    zentao_link = f"{cfg.zentao.base_url.rstrip('/')}/bug-browse-{vi.get('product_id', '')}-0-all.html"
    zh_line = f"\n> 按版本筛选查阅：[{vi.get('build', '')} 缺陷列表（禅道）]({zentao_link})\n"
    md = md.rstrip("\n") + "\n" + zh_line

    out = Path("output/preview_V5.2.0_渲染增强预览.md")
    out.write_text(md, encoding="utf-8")
    print(f"[完成] 预览产物：{out}")
    print(f"  · 标题行：{title}")
    print(f"  · 4.1.1「/」行：保持原样（改名增强已按拍板撤销）")
    print(f"  · 4.3/4.4：保持原样（说明行增强已按拍板去除）")
    print(f"  · §6 链接：{zentao_link}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
