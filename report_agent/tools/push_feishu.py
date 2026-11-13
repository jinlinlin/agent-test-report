"""F9 飞书推送工具（V2 技术验证脚手架）。

将渲染好的 Markdown 报告导入为飞书云文档，并按配置写入知识库指定节点，返回文档链接。
- 由循环注入 report_markdown 与 version_name（与 compute_statistics/render_report 同构）；
- 纯确定性调用，LLM 不参与；
- 任何失败都返回 ok=False，由 loop 记为 feishu_error，**不推翻已生成的报告**（防幻觉兜底）。
"""
from __future__ import annotations

from .base import Tool, ToolResult
from .feishu_client import FeishuClient, FeishuError
from ..report.docx_export import md_to_docx_bytes


def _doc_name(version_name: str) -> str:
    base = (version_name or "report").strip()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base)
    return f"{safe or 'report'}_测试报告"


class PushFeishuTool(Tool):
    name = "push_feishu"
    description = "将渲染好的 Markdown 报告导入为飞书云文档并写入知识库节点（多项目：按产品名路由同名子节点；同项目下按版本号建子目录），返回文档链接。由循环注入 report_markdown、version_name、product_name、build 与 stats_obj（用于第 6 节内嵌多维表格）。"
    parameters = {"report_markdown": "string(注入)", "version_name": "string(注入)", "product_name": "string(注入)", "build": "string(注入)", "stats_obj": "object(注入)"}

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def run(self, report_markdown: str = "", version_name: str = "", product_name: str = "", build: str = "", stats_obj=None, **kwargs) -> ToolResult:
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
            # 优先本地 md→docx（pandoc 将相对路径图表内嵌进 docx，飞书导入才不丢图）；
            # 开关关闭、pandoc 缺失或转换失败时回退直接导入 md（文字/表格照常、仅图丢失，不阻断）。
            docx_bytes = md_to_docx_bytes(report_markdown, self.cfg.report_dir,
                                          image_scale=f.chart_display_scale) if f.import_as_docx else None
            if docx_bytes is not None:
                upload_name = name + ".docx"
                content = docx_bytes
                ext = "docx"
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                upload_name = name + ".md"
                content = report_markdown.encode("utf-8")
                ext = "md"
                mime = "text/markdown"
            file_token = client.upload_bytes(upload_name, content, f.upload_folder_token, mime=mime)
            ticket = client.create_import_task(file_token, name, f.upload_folder_token, file_extension=ext)
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
            # C：导入后将第 6 节缺陷清单内嵌为文档内多维表格块（非致命，失败不影响已推送的报告）。
            embed_app = self._embed_defects(client, f, doc_token, stats_obj)
            # 样式：正文各表格首行标记为飞书内置标题行（整格底色；格级背景色 API 不支持，实测）。
            header_row = self._mark_table_headers(client, f, doc_token)
            # 样式：按版式补打表格列宽（docx 导入会把 column_width 重置为等宽，须导入后用块 API 补打；非致命）。
            col_widths = self._apply_table_column_widths(client, f, doc_token)
            return ToolResult(ok=True, data=url,
                              meta={"doc_token": doc_token, "target": f.target, "import_format": ext,
                                    "defect_bitable_embed": embed_app,
                                    "table_header_row": header_row,
                                    "table_column_widths": col_widths})
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

    @staticmethod
    def _mark_table_headers(client: FeishuClient, f, doc_token: str) -> bool:
        """导入后把正文各 docx 表格首行标记为飞书内置标题行（用户 2026-11-13 选定）。

        飞书 docx OpenAPI 无格级背景色（实测：table_cell 无样式字段、batch_update 子请求均不支持），
        格内文本块背景色只染文字周围（高亮条形态被用户否决），故改用内置标题行——整格填满，
        颜色为飞书内置不可单独指定。feishu.table_header_row=false 时一次网络调用都不发。
        任何异常吞掉返回 False，不阻断已推送的报告。内嵌多维表格有自己的表头样式，不受影响。"""
        if not getattr(f, "table_header_row", True):
            return False
        try:
            return client.mark_table_header_rows(doc_token) > 0
        except Exception:
            return False

    @staticmethod
    def _apply_table_column_widths(client: FeishuClient, f, doc_token: str) -> bool:
        """导入后把报告已知版式的表格列宽按内容比例补打（用户 2026-11-13 要求）。

        docx 导入会把列宽重置为等宽（实测），md 分隔行 dash 比例不落地，故用飞书块 API
        update_table_property 补打。feishu.table_column_widths=false 时一次网络调用都不发。
        任何异常吞掉返回 False，不阻断已推送的报告。"""
        if not getattr(f, "table_column_widths", True):
            return False
        try:
            return client.set_table_column_widths(doc_token)
        except Exception:
            return False

    @staticmethod
    def _embed_defects(client: FeishuClient, f, doc_token: str, stats_obj) -> str:
        """C 方案：将两类重点缺陷内嵌为文档内多维表格块（追到报告正文末尾），每张表前配一个标题块。

        顺序：①「当前版本遗留未解决bug」（激活待解决）②「当前版本延期处理bug」（延期处理）。仅当 defect_bitable +
        defect_bitable_embed 开启且有缺陷行时执行；任何异常（权限/网络/未发版）一律返回空串，正文保留第 6 节计数
        引导不阻断报告。成功返回各内嵌表 app_token 的逗号拼接。"""
        if stats_obj is None or not (getattr(f, "defect_bitable", False) and getattr(f, "defect_bitable_embed", False)):
            return ""
        active = getattr(stats_obj, "active_bugs", None) or []
        postponed = getattr(stats_obj, "postponed_bugs", None) or []
        if not (active or postponed):
            return ""
        from ..report.renderer import DEFECT_FIELD_SPECS, bug_to_fields
        # 空类不内嵌对应标题块+表（与正文第 6 节「空类不展示」同口径，用户 2026-11-13 要求），剩下类顺次重编号
        specs = [("当前版本遗留未解决bug", active), ("当前版本延期处理bug", postponed)]
        groups = [
            (f"6.{i} {title}", [bug_to_fields(r, j + 1) for j, r in enumerate(rows)])
            for i, (title, rows) in enumerate([(t, rs) for t, rs in specs if rs], 1)
        ]
        try:
            apps = client.embed_defect_bitable(doc_token, groups, DEFECT_FIELD_SPECS)
            return ",".join(app for app, _tbl in apps)
        except Exception:
            return ""
