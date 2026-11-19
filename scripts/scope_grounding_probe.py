"""V2-4 只读探针：真实读取飞书某版本目录下的测试用例表，检查能否提取 2.1 测试范围。

用法: python scripts/scope_grounding_probe.py <product> <build>
仅读不写；打印版本目录下各节点 obj_type，先试上传的 .xlsx（file），未命中再试飞书原生电子表格（sheet），
打印提取结果与模块数，并按 scope.module_groups（人工确认的一级菜单映射）打印归并后的行数。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.config import load_config  # noqa: E402
from report_agent.report.cases import (  # noqa: E402
    apply_module_groups, extract_scope_from_cases, extract_scope_from_rows)
from report_agent.tools.feishu_client import FeishuClient, FeishuError  # noqa: E402


def main():
    product = sys.argv[1] if len(sys.argv) > 1 else "UniResearch"
    build = sys.argv[2] if len(sys.argv) > 2 else "V5.1.0"

    cfg = load_config(**{"feishu.enabled": True})
    f = cfg.feishu
    print(f"[配置] enabled={f.enabled} space={f.wiki_space_id} 容器={f.wiki_projects_parent}")
    if not (f.app_id and f.app_secret and f.wiki_space_id and f.wiki_projects_parent):
        print("[失败] 飞书凭据/知识库落点未配齐（.env）")
        return 2

    c = FeishuClient(f)
    try:
        proj = c.find_child_by_title(f.wiki_space_id, f.wiki_projects_parent, product)
        print(f"[产品节点] {proj or '(未命中)'}")
        if not proj:
            return 1
        ver = c.find_child_node(f.wiki_space_id, proj, build)
        print(f"[版本节点] {ver or '(未命中)'}")
        if not ver:
            return 1

        print("[版本目录子节点]")
        for n in c.list_nodes(f.wiki_space_id, ver):
            print(f"   obj_type={str(n.get('obj_type')):<10} obj_token={str(n.get('obj_token')):<28} 标题={n.get('title')}")

        # 数据源①：测试用例 xlsx（obj_type=file）
        case_token = c.find_doc_obj_token(product, build, "测试用例", obj_type="file")
        print(f"[测试用例 file] obj_token={case_token or '(无)'}")
        if case_token:
            data = c.download_file(case_token)
            print(f"[下载] {len(data)} 字节")
            print(f"[用例表提取范围] {extract_scope_from_cases(data)}")
            return 0

        # 数据源②：飞书原生电子表格（obj_type=sheet）
        sheet_token = c.find_doc_obj_token(product, build, "测试用例", obj_type="sheet")
        print(f"[测试用例 sheet] obj_token={sheet_token or '(无)'}")
        if not sheet_token:
            return 0
        rows = c.read_sheet_rows(sheet_token)
        filled = [r for r in rows if any(str(x or "").strip() for x in r)]
        print(f"[读值] 非空行 {len(filled)}（含表头）表头={filled[0] if filled else '(空)'}")
        scope = extract_scope_from_rows(filled)
        print(f"[用例表提取范围] 模块 {len(scope or [])} 个 / 用例 {sum(r['case_count'] for r in scope or [])} 条")
        for r in (scope or [])[:10]:
            print(f"   {r['module']}（{r['case_count']}）: {r['desc'][:60]}")
        grouped = apply_module_groups(scope, cfg.scope.module_groups)
        print(f"[映射归并后] {len(grouped or [])} 行（共 {sum(r['case_count'] for r in grouped or [])} 条用例）")
        for r in (grouped or []):
            print(f"   {r['module']}（{r['case_count']}）: {r['desc'][:70]}")
    except FeishuError as exc:
        print(f"[飞书错误] ({exc.kind}) {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
