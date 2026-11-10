"""F9 飞书推送工具（V2 技术验证脚手架）。

将渲染好的 Markdown 报告导入为飞书云文档，并按配置写入知识库指定节点，返回文档链接。
- 由循环注入 report_markdown 与 version_name（与 compute_statistics/render_report 同构）；
- 纯确定性调用，LLM 不参与；
- 任何失败都返回 ok=False，由 loop 记为 feishu_error，**不推翻已生成的报告**（防幻觉兜底）。
"""
from __future__ import annotations

from .base import Tool, ToolResult
from .feishu_client import FeishuClient, FeishuError


def _doc_name(version_name: str) -> str:
    base = (version_name or "report").strip()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base)
    return f"{safe or 'report'}_测试报告"


class PushFeishuTool(Tool):
    name = "push_feishu"
    description = "将渲染好的 Markdown 报告导入为飞书云文档并写入知识库节点（多项目：按产品名路由同名子节点；同项目下按版本号建子目录），返回文档链接。由循环注入 report_markdown、version_name、product_name 与 build。"
    parameters = {"report_markdown": "string(注入)", "version_name": "string(注入)", "product_name": "string(注入)", "build": "string(注入)"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, report_markdown: str = "", version_name: str = "", product_name: str = "", build: str = "", **kwargs) -> ToolResult:
        f = self.cfg.feishu

        # 前置校验：任一不满足都如实返回失败，不做任何伪造
        if not f.enabled:
            return ToolResult(ok=False, error="飞书推送未启用（--push-feishu 或 feishu.enabled=true）")
        if not report_markdown:
            return ToolResult(ok=False, error="报告内容为空，无法推送")
        if not (f.app_id and f.app_secret):
            return ToolResult(ok=False, error="缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        if not f.upload_folder_token:
            return ToolResult(ok=False, error="缺少 FEISHU_FOLDER_TOKEN（导入的上传落点）")
        if f.target == "wiki" and not f.wiki_space_id:
            return ToolResult(ok=False, error="知识库模式需配置 FEISHU_WIKI_SPACE_ID")
        if f.target == "wiki" and not (f.wiki_projects_parent or f.wiki_parent_node):
            return ToolResult(ok=False, error="知识库模式需配置落点节点（FEISHU_WIKI_PROJECTS_PARENT 或 FEISHU_WIKI_PARENT_NODE）")

        client = FeishuClient(f)
        name = _doc_name(version_name or product_name)
        try:
            client.get_tenant_token()
            file_token = client.upload_bytes(name + ".md", report_markdown.encode("utf-8"), f.upload_folder_token)
            ticket = client.create_import_task(file_token, name, f.upload_folder_token)
            result = client.poll_import_result(ticket)
            doc_token = result.get("token", "")
            if not doc_token:
                return ToolResult(ok=False, error="导入完成但未返回文档 token")

            if f.target == "wiki":
                project_parent = self._resolve_parent(client, f, product_name)
                if not project_parent:
                    return ToolResult(ok=False, error="未能确定知识库落点节点（产品无同名子节点且未配置默认落点）")
                target_parent = self._resolve_version_node(client, f, project_parent, build)
                node_token = client.move_doc_to_wiki(f.wiki_space_id, target_parent, doc_token)
                url = f"{f.doc_base_url.rstrip('/')}/wiki/{node_token}"
            else:
                url = f"{f.doc_base_url.rstrip('/')}/docx/{doc_token}"
            return ToolResult(ok=True, data=url, meta={"doc_token": doc_token, "target": f.target})
        except FeishuError as exc:
            return ToolResult(ok=False, error=f"飞书推送失败：{exc}")

    @staticmethod
    def _resolve_parent(client: FeishuClient, f, product_name: str) -> str:
        """多项目落点：在“项目容器节点”下按产品名找同名子节点；未命中回退默认落点。"""
        if f.wiki_projects_parent and product_name:
            hit = client.find_child_by_title(f.wiki_space_id, f.wiki_projects_parent, product_name)
            if hit:
                return hit
        return f.wiki_parent_node

    @staticmethod
    def _resolve_version_node(client: FeishuClient, f, project_parent: str, build: str) -> str:
        """版本目录：在项目节点下找同名版本子节点（如 V5.0.0），命中则复用；缺失则新建。

        无版本号、或新建失败（权限/异常）时回退为项目节点，不推翻报告。
        """
        if not build or not project_parent:
            return project_parent
        hit = client.find_child_node(f.wiki_space_id, project_parent, build)
        if hit:
            return hit
        try:
            created = client.create_node(f.wiki_space_id, project_parent, build)
        except FeishuError:
            return project_parent
        return created or project_parent
