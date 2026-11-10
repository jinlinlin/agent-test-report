"""查询飞书知识库 space_id 与节点 node_token（配合 push_feishu 落地）。

用法:
  python scripts/feishu_wiki_probe.py                              # 列出所有知识库空间(space_id + 名称)
  python scripts/feishu_wiki_probe.py <space_id>                   # 列出该空间根节点
  python scripts/feishu_wiki_probe.py <space_id> <父node_token>     # 列出该节点下的子节点
  python scripts/feishu_wiki_probe.py --node <node_token>          # 用 URL 里的 node_token 反查 space_id/标题/obj_token

前置: config/.env 配好 FEISHU_APP_ID / FEISHU_APP_SECRET，且应用已开通 wiki 权限并发布版本。
接口路径以飞书开放平台官方文档为准。
"""
import os
import sys

# 保证能 import report_agent（从仓库根运行时无需此步，双保险）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.config import load_config                      # noqa: E402
from report_agent.tools.feishu_client import FeishuClient, FeishuError  # noqa: E402


def _client() -> FeishuClient:
    cfg = load_config()
    return FeishuClient(cfg.feishu)


def list_spaces(client: FeishuClient):
    print("=== 知识库空间(space_id) ===")
    for it in client.list_spaces():
        print(f"  space_id={it.get('space_id')}  名称={it.get('name')}")
    print("\n下一步: python scripts/feishu_wiki_probe.py <space_id> 查看根节点")


def list_nodes(client: FeishuClient, space_id: str, parent: str = ""):
    print(f"=== 空间 {space_id} 节点，父={parent or '(根节点)'} ===")
    for n in client.list_nodes(space_id, parent):
        print(f"  node_token={n.get('node_token')}  obj_token={n.get('obj_token')}  "
              f"类型={n.get('obj_type')}  标题={n.get('title')}  有子节点={n.get('has_child')}")
    print("\n要找更深的节点: python scripts/feishu_wiki_probe.py <space_id> <父node_token>")


def resolve_node(client: FeishuClient, node_token: str):
    n = client.resolve_node(node_token)
    print("=== 节点反查结果 ===")
    print(f"node_token = {n.get('node_token')}")
    print(f"space_id   = {n.get('space_id')}    ← 填 FEISHU_WIKI_SPACE_ID")
    print(f"标题       = {n.get('title')}         ← 确认是不是 'Uniresearch项目测试'")
    print(f"obj_type   = {n.get('obj_type')}")
    print(f"obj_token  = {n.get('obj_token')}     ← 该节点背后 docx 的 token")
    print(f"父节点     = {n.get('parent_node_token')}")


def main():
    args = sys.argv[1:]
    try:
        client = _client()
        if args and args[0] == "--node":
            if len(args) < 2:
                print("用法: python scripts/feishu_wiki_probe.py --node <node_token>")
                return 1
            resolve_node(client, args[1])
        elif not args:
            list_spaces(client)
        elif len(args) == 1:
            list_nodes(client, args[0])
        else:
            list_nodes(client, args[0], args[1])
    except FeishuError as exc:
        print(f"[失败] ({exc.kind}) {exc}")
        print("排查：.env 是否配好 app_id/secret；wiki:wiki 权限是否开通并发版；应用是否已加为知识库成员。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
