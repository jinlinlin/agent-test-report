"""把任意本地 Markdown 导入为飞书云文档并挂到知识库节点（一次性快速脚本）。

复用 F9 推送链路：上传 Drive 文件夹 → 导入任务 md→docx → 轮询结果 → 三步落点移入知识库。
本文档类型无本地图片，直接导 md，不需要 pandoc 转 docx。

用法：
    python scripts/import_md_to_feishu.py "docs/测试报告生成Agent V1.0 需求文档.md"
    可选：--name 自定义文档标题；--parent 指定知识库父节点 node_token（默认 FEISHU_WIKI_PARENT_NODE）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_agent.config import load_config
from report_agent.tools.feishu_client import FeishuClient, FeishuError


def main() -> int:
    ap = argparse.ArgumentParser(description="导入本地 md 到飞书知识库")
    ap.add_argument("md_path", help="本地 Markdown 文件路径")
    ap.add_argument("--name", default="", help="飞书文档标题（默认取文件名）")
    ap.add_argument("--parent", default="", help="知识库父节点 node_token（默认配置落点）")
    args = ap.parse_args()

    path = Path(args.md_path)
    if not path.is_file():
        print(f"[错误] 文件不存在：{path}")
        return 1

    cfg = load_config()
    f = cfg.feishu
    if not (f.app_id and f.app_secret and f.upload_folder_token and f.wiki_space_id):
        print("[错误] .env 飞书凭据/落点配置不全")
        return 1

    name = args.name or path.stem
    parent = args.parent or f.wiki_parent_node
    if not parent:
        print("[错误] 未指定知识库父节点（--parent 或 FEISHU_WIKI_PARENT_NODE）")
        return 1

    client = FeishuClient(f)
    try:
        client.get_tenant_token()
        print(f"[1/4] 上传 {path.name} ({len(path.read_bytes())} bytes) ...")
        file_token = client.upload_bytes(name + ".md", path.read_bytes(),
                                         f.upload_folder_token, mime="text/markdown")
        print(f"[2/4] 发起导入任务 (file_token={file_token}) ...")
        ticket = client.create_import_task(file_token, name, f.upload_folder_token,
                                           file_extension="md")
        print(f"[3/4] 轮询导入结果 (ticket={ticket}) ...")
        result = client.poll_import_result(ticket)
        doc_token = result.get("token", "")
        if not doc_token:
            print(f"[错误] 导入完成但未返回文档 token：{result}")
            return 1
        print(f"[4/4] 移入知识库节点 {parent} ...")
        node_token = client.move_doc_to_wiki(f.wiki_space_id, parent, doc_token)
        url = f"{f.doc_base_url.rstrip('/')}/wiki/{node_token}"
        print(f"[完成] 飞书文档链接：{url}")
        return 0
    except FeishuError as exc:
        print(f"[失败] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
