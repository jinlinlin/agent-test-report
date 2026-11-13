"""V2-4 只读探针：真实读取飞书某版本目录下的测试用例 xlsx，检查能否提取 2.1 测试范围。

用法: python scripts/scope_grounding_probe.py <product> <build>
仅读不写；打印版本目录下各节点 obj_type，并下载测试用例 xlsx 打印 extract_scope_from_cases 结果。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.config import load_config  # noqa: E402
from report_agent.report.cases import extract_scope_from_cases  # noqa: E402
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

        # 数据源：测试用例 xlsx（obj_type=file）
        case_token = c.find_doc_obj_token(product, build, "测试用例", obj_type="file")
        print(f"[测试用例 file] obj_token={case_token or '(无)'}")
        if not case_token:
            return 0
        data = c.download_file(case_token)
        print(f"[下载] {len(data)} 字节")
        rows = extract_scope_from_cases(data)
        print(f"[用例表提取范围] {rows}")
    except FeishuError as exc:
        print(f"[飞书错误] ({exc.kind}) {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
