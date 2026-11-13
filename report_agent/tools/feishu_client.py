"""飞书开放平台 REST 封装（V2 技术验证脚手架）。

职责：把「Markdown 报告 → 飞书云文档 → 写入知识库」链路上的接口调用集中到这里，
仿 zentao_client.py 的风格：统一异常 FeishuError，只读式封装，便于上层工具复用与替换。

接口端点名/字段以飞书开放平台当前文档为准，落地时若报错请核对（见 README 飞书接入说明）。
交付路径区分两套体系：
- 云空间 Drive：upload_all + import_tasks（folder_token 定位）
- 知识库 Wiki：move_docs_to_wiki（space_id + node_token 定位），需 wiki:wiki 权限且应用为知识库成员
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class FeishuError(Exception):
    """飞书访问异常。kind: auth | error | not_found | timeout"""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def _requests():
    try:
        import requests
        return requests
    except ImportError as exc:  # pragma: no cover
        raise FeishuError("缺少 requests 依赖，请先 pip install -r requirements.txt", "error") from exc


def _split_bitable_token(token: str) -> tuple:
    """内嵌多维表格块的 bitable.token 形如 BitableToken_TableID（TableID 以 tbl 开头），拆出 (app_token, table_id)。"""
    if "_tbl" in token:
        app, tbl = token.split("_tbl", 1)
        return app, "tbl" + tbl
    if "_" in token:
        app, tbl = token.rsplit("_", 1)
        return app, tbl
    return token, ""


class FeishuClient:
    def __init__(self, feishu_cfg) -> None:
        self.f = feishu_cfg
        self._token: Optional[str] = None

    # ---- 内部 ----
    def _url(self, path: str) -> str:
        return self.f.api_base.rstrip("/") + path

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        requests = _requests()
        try:
            resp = requests.get(self._url(path), headers=self._auth_header(), params=params or {}, timeout=30)
        except requests.exceptions.Timeout as exc:
            raise FeishuError(f"飞书接口超时：{path}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书接口连接失败：{exc}", "error") from exc
        return self._check(resp, path)

    def _post(self, path: str, json_body: Optional[Dict[str, Any]] = None,
              data: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        requests = _requests()
        headers = self._auth_header() if json_body is not None else {"Authorization": self._bearer()}
        try:
            resp = requests.post(self._url(path), headers=headers, json=json_body,
                                 data=data, files=files, timeout=60)
        except requests.exceptions.Timeout as exc:
            raise FeishuError(f"飞书接口超时：{path}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书接口连接失败：{exc}", "error") from exc
        return self._check(resp, path)

    def _put(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._send("put", path, json_body)

    def _patch(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._send("patch", path, json_body)

    def _delete(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._send("delete", path, json_body)

    def _send(self, method: str, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        requests = _requests()
        try:
            resp = getattr(requests, method)(self._url(path), headers=self._auth_header(),
                                             json=json_body, timeout=60)
        except requests.exceptions.Timeout as exc:
            raise FeishuError(f"飞书接口超时：{path}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书接口连接失败：{exc}", "error") from exc
        return self._check(resp, path)

    def _check(self, resp, path: str) -> Dict[str, Any]:
        if resp.status_code == 404:
            raise FeishuError(f"飞书接口不存在（404）：{path}", "not_found")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FeishuError(f"飞书接口返回非 JSON：{path}", "error") from exc
        code = payload.get("code")
        if code not in (0, None):
            raise FeishuError(f"飞书接口错误 [{path}] code={code} msg={payload.get('msg')}", "error")
        return payload

    def _bearer(self) -> str:
        if not self._token:
            self._token = self.get_tenant_token()
        return f"Bearer {self._token}"

    def _auth_header(self) -> Dict[str, str]:
        return {"Authorization": self._bearer()}

    # ---- 认证 ----
    def get_tenant_token(self) -> str:
        if self._token:
            return self._token
        if not (self.f.app_id and self.f.app_secret):
            raise FeishuError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET", "auth")
        requests = _requests()
        try:
            resp = requests.post(self._url("/auth/v3/tenant_access_token/internal"),
                                 json={"app_id": self.f.app_id, "app_secret": self.f.app_secret}, timeout=15)
            payload = resp.json()
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书认证连接失败：{exc}", "error") from exc
        except ValueError as exc:
            raise FeishuError("飞书认证返回非 JSON", "error") from exc
        if payload.get("code") != 0:
            raise FeishuError(f"换取 tenant_access_token 失败 code={payload.get('code')} msg={payload.get('msg')}", "auth")
        self._token = payload.get("tenant_access_token", "")
        if not self._token:
            raise FeishuError("飞书未返回 tenant_access_token", "auth")
        return self._token

    # ---- 云空间 Drive：上传 + 导入 ----
    def upload_bytes(self, name: str, content: bytes, folder_token: str, mime: str = "text/markdown") -> str:
        """把文件字节上传到云空间文件夹，返回 file_token（mime 随文件类型变化）。"""
        requests = _requests()
        url = self._url("/drive/v1/files/upload_all")
        form = {"file_name": name, "parent_type": "explorer", "parent_node": folder_token, "size": str(len(content))}
        files = {"file": (name, content, mime)}
        try:
            resp = requests.post(url, headers={"Authorization": self._bearer()}, data=form, files=files, timeout=60)
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书上传失败：{exc}", "error") from exc
        payload = self._check(resp, "/drive/v1/files/upload_all")
        return payload.get("data", {}).get("file_token", "")

    def download_file(self, file_token: str) -> bytes:
        """下载云空间/知识库中一个文件节点的原始字节（GET /drive/v1/files/{file_token}/download）。

        用于读取以 `obj_type=file` 上传的附件（如测试用例 .xlsx）。无 token 返回空字节；
        404/非 2xx 抛 FeishuError 由上层回退。**只读**，不修改任何文件。
        """
        if not file_token:
            return b""
        requests = _requests()
        path = f"/drive/v1/files/{file_token}/download"
        try:
            resp = requests.get(self._url(path), headers=self._auth_header(), timeout=60)
        except requests.exceptions.RequestException as exc:
            raise FeishuError(f"飞书下载失败：{exc}", "error") from exc
        if resp.status_code == 404:
            raise FeishuError(f"飞书文件不存在（404）：{path}", "not_found")
        if resp.status_code not in (200, 206):
            # 非二进制成功响应（如权限不足返回 JSON 错误体）统一按错误抛出
            raise FeishuError(f"飞书下载失败 HTTP {resp.status_code}：{resp.text[:200]}", "error")
        return resp.content

    def create_import_task(self, file_token: str, name: str, folder_token: str, file_extension: str = "md") -> str:
        """发起导入任务（file_extension=md/docx → 云 docx），返回 ticket。"""
        body = {
            "file_extension": file_extension,
            "file_token": file_token,
            "name": name,
            "type": "docx",
            "point": {"mount_type": 1, "mount_key": folder_token},
        }
        payload = self._post("/drive/v1/import_tasks", json_body=body)
        return payload.get("data", {}).get("ticket", "")

    def poll_import_result(self, ticket: str) -> Dict[str, Any]:
        """轮询导入结果，成功返回 {token, token_type, title}；超时/失败抛 FeishuError。"""
        deadline = time.time() + max(self.f.import_poll_seconds, 5)
        path = f"/drive/v1/import_tasks/{ticket}"
        while time.time() < deadline:
            payload = self._get(path)
            result = payload.get("data", {}).get("result", {})
            status = result.get("job_status")
            if status == 0:  # 成功
                return result
            if status not in (1, 2, None):  # 1/2 处理中
                raise FeishuError(f"导入失败 job_status={status} msg={result.get('job_error_msg')}", "error")
            time.sleep(2)
        raise FeishuError("导入超时未完成", "timeout")

    # ---- 知识库 Wiki ----
    def move_doc_to_wiki(self, space_id: str, parent_node_token: str, doc_token: str) -> str:
        """把导入生成的 docx 移入知识库并挂到目标父节点下，返回新节点 node_token。

        实测：新版 move_docs_to_wiki 是异步接口，只把文档放到知识库根节点、忽略父节点参数，
        因此需三步：① move_docs_to_wiki 移入库 → ② 按 obj_token 轮询定位新节点 → ③ move 到目标父节点。
        """
        self._post(f"/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki",
                   json_body={"obj_type": "docx", "obj_token": doc_token})
        node_token = self._wait_for_node(space_id, parent_node_token, doc_token)
        if not node_token:
            raise FeishuError("移入知识库后未找到对应节点（obj_token 匹配超时，检查应用是否为知识库成员）", "timeout")
        # 若未直接落在目标父节点下，显式移动一次
        node = self.resolve_node(node_token)
        if node.get("parent_node_token") != parent_node_token:
            self._post(f"/wiki/v2/spaces/{space_id}/nodes/{node_token}/move",
                       json_body={"target_parent_token": parent_node_token})
        return node_token

    def _wait_for_node(self, space_id: str, parent_node_token: str, obj_token: str) -> str:
        """轮询知识库根层级与目标父层级，按 obj_token 定位新节点 node_token；超时返回空串。"""
        deadline = time.time() + max(self.f.import_poll_seconds, 10)
        while time.time() < deadline:
            for parent in ("", parent_node_token):
                for n in self.list_nodes(space_id, parent):
                    if n.get("obj_token") == obj_token:
                        return n.get("node_token", "")
            time.sleep(2)
        return ""

    def resolve_node(self, node_token: str) -> Dict[str, Any]:
        """用 node_token 反查节点信息（space_id / title / obj_token / parent_node_token）。"""
        payload = self._get("/wiki/v2/spaces/get_node", params={"token": node_token, "token_type": "node_token"})
        return payload.get("data", {}).get("node", {})

    # ---- 多维表格 Bitable 基础写操作（供 C 内嵌复用）----
    def batch_create_records(self, app_token: str, table_id: str,
                             records: List[Dict[str, Any]], chunk: int = 500) -> None:
        """批量写入记录（每批≤500，避开单次上限），records 元素为 {列名: 值}。需 base:record:create。"""
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        for i in range(0, len(records), chunk):
            self._post(path, json_body={"records": [{"fields": rec} for rec in records[i:i + chunk]]})

    # ---- 文档内嵌多维表格块（C 方案：第 6 节缺陷清单直接展开在正文）----
    def insert_bitable_block(self, document_id: str) -> tuple:
        """在文档末尾插入一个内嵌空多维表格块（block_type=18, view_type=1 数据表），返回 (app_token, table_id)。

        需 docx:document（创建及编辑新版文档）。块返回的 bitable.token 形如 BitableToken_TableID，
        拆出的 app_token/table_id 可被独立多维表格 API 直接读写（已实测）。
        """
        payload = self._post(f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                             json_body={"children": [{"block_type": 18, "bitable": {"view_type": 1}}]})
        children = payload.get("data", {}).get("children", []) or []
        token = ((children[0] if children else {}).get("bitable") or {}).get("token", "") if children else ""
        if not token:
            raise FeishuError("插入多维表格块未返回 bitable.token")
        return _split_bitable_token(token)

    def insert_heading_block(self, document_id: str, text: str, level: int = 3) -> None:
        """在文档末尾追加一个标题块（默认三级，block_type=level+2：1级=3、3级=5），用于为内嵌表配标题。

        需 docx:document。失败上抛由调用方（非致命）处理。
        """
        if not text:
            return
        key = f"heading{level}"
        child = {"block_type": level + 2, key: {"elements": [{"text_run": {"content": text}}]}}
        self._post(f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                   json_body={"children": [child]})

    def list_fields(self, app_token: str, table_id: str) -> List[Dict[str, Any]]:
        """列出数据表字段（含 field_id/is_primary/type），用于清理内嵌表默认列。"""
        items: List[Dict[str, Any]] = []
        page = ""
        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if page:
                params["page_token"] = page
            data = self._get(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params=params).get("data", {})
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page = data.get("page_token", "")
        return items

    def create_field(self, app_token: str, table_id: str, spec: Dict[str, Any]) -> str:
        """向已存在的数据表新增一个字段（spec 形如 {field_name, type[, property]}），返回 field_id。需 base:field:create。"""
        payload = self._post(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", json_body=dict(spec))
        return payload.get("data", {}).get("field", {}).get("field_id", "")

    def update_field(self, app_token: str, table_id: str, field_id: str, field_name: str, ftype: int) -> None:
        """更新字段名/类型（用于把内嵌表默认主列改名为我们的首列）。需 base:field:update。"""
        self._put(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
                  json_body={"field_name": field_name, "type": ftype})

    def delete_field(self, app_token: str, table_id: str, field_id: str) -> None:
        """删除字段（用于清理内嵌表自带的多余默认列，主列不可删）。需 base:field:delete。"""
        self._delete(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}")

    def list_record_ids(self, app_token: str, table_id: str) -> List[str]:
        """列出数据表全部记录 ID（用于清理内嵌表自带的默认空行）。"""
        ids: List[str] = []
        page = ""
        while True:
            params: Dict[str, Any] = {"page_size": 500}
            if page:
                params["page_token"] = page
            data = self._get(f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", params=params).get("data", {})
            ids.extend(r.get("record_id", "") for r in (data.get("items", []) or []) if r.get("record_id"))
            if not data.get("has_more"):
                break
            page = data.get("page_token", "")
        return ids

    def delete_records(self, app_token: str, table_id: str, record_ids: List[str], chunk: int = 500) -> None:
        """批量删除记录（每批≤500），record_ids 为记录 ID 列表。需 base:record:delete。"""
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        for i in range(0, len(record_ids), chunk):
            self._post(path, json_body={"records": record_ids[i:i + chunk]})

    def rename_table(self, app_token: str, table_id: str, name: str) -> None:
        """重命名数据表（多维表格无独立建表名接口，内嵌默认表建好后用 PATCH 改名）。需 base:table:update。"""
        if name:
            self._patch(f"/bitable/v1/apps/{app_token}/tables/{table_id}", json_body={"name": name})

    def embed_defect_bitable(self, document_id: str, groups: List[tuple], field_specs: List[Dict[str, Any]]) -> List[tuple]:
        """为每个分组各插入一个内嵌多维表格块并灌数据，返回各块的 (app_token, table_id) 列表。

        每个块绑定自己的一张表（改名/清理默认列与空行/建列/写记录），使「延期处理清单」与「激活待解决清单」
        在正文各自独立展开——而非单块内多表（单块只渲染其绑定的默认表，其余表不会显示）。异常上抛由上层捕获回退。
        需 docx:document + base:field/record/table 增删改。
        """
        result: List[tuple] = []
        for name, records in groups:
            self.insert_heading_block(document_id, name)  # 先插标题块，再插其下的内嵌表
            app, tbl = self.insert_bitable_block(document_id)
            self._fill_embed_table(app, tbl, name, records, field_specs)
            result.append((app, tbl))
        return result

    def _fill_embed_table(self, app: str, tbl: str, name: str,
                          records: List[Dict[str, Any]], field_specs: List[Dict[str, Any]]) -> None:
        """把一个内嵌块自带的默认表清理并改造成目标数据表：主列改名为首字段、删多余默认列、删默认空行、
        补建其余列、表改名、灌入记录。"""
        # 1) 清理默认表列：主列改名为首字段，删除其余默认列
        fields = self.list_fields(app, tbl)
        primary = next((f for f in fields if f.get("is_primary")), None)
        head = field_specs[0]
        if primary:
            self.update_field(app, tbl, primary["field_id"], head["field_name"], head.get("type", 1))
            for f in fields:
                if not f.get("is_primary") and f.get("field_id"):
                    self.delete_field(app, tbl, f["field_id"])
        else:  # 异常兜底：无主列则全部新建
            self.create_field(app, tbl, dict(head))
        # 2) 补建其余字段
        for spec in field_specs[1:]:
            self.create_field(app, tbl, dict(spec))
        # 3) 删除默认空行
        junk_ids = self.list_record_ids(app, tbl)
        if junk_ids:
            self.delete_records(app, tbl, junk_ids)
        # 4) 改名 + 灌记录
        self.rename_table(app, tbl, name)
        if records:
            self.batch_create_records(app, tbl, records)

    def list_spaces(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = ""
        while True:
            params = {"page_size": 50}
            if page:
                params["page_token"] = page
            payload = self._get("/wiki/v2/spaces", params=params)
            data = payload.get("data", {})
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page = data.get("page_token", "")
        return items

    def find_child_node(self, space_id: str, parent_node_token: str, title: str) -> str:
        """在父节点下按标题**精确**查找子节点（归一化后全等），返回 node_token（无则空串）。

        用于版本目录：在项目节点下找与版本号同名的子节点（如 V5.0.0），命中则复用不重建。
        """
        def _n(s: str) -> str:
            s = str(s or "").lower()
            for ch in "“”\"'  \t\u3000":
                s = s.replace(ch, "")
            return s

        needle = _n(title)
        if not needle:
            return ""
        for node in self.list_nodes(space_id, parent_node_token):
            if _n(node.get("title", "")) == needle:
                return node.get("node_token", "")
        return ""

    def create_node(self, space_id: str, parent_node_token: str, title: str) -> str:
        """在父节点下新建一个空白 docx 知识节点（用作版本目录），返回新 node_token。

        需 wiki:node:create 或 wiki:wiki 权限且对父节点有编辑权；失败抛 FeishuError 由上层回退处理。
        """
        payload = self._post(f"/wiki/v2/spaces/{space_id}/nodes",
                             json_body={"obj_type": "docx", "parent_node_token": parent_node_token,
                                        "node_type": "origin", "title": title})
        return payload.get("data", {}).get("node", {}).get("node_token", "")

    def find_child_by_title(self, space_id: str, parent_node_token: str, name: str) -> str:
        """在容器节点下按标题找与产品名匹配的子节点，返回 node_token（无则空串）。

        归一化后互为包含即候选（如产品名 UniResearch ↔ 子节点标题 Uniresearch项目测试），取标题最长者。
        """
        def _n(s: str) -> str:
            s = str(s or "").lower()
            for ch in "“”\"'  \t\u3000":
                s = s.replace(ch, "")
            return s

        needle = _n(name)
        if not needle:
            return ""
        best_token, best_len = "", -1
        for node in self.list_nodes(space_id, parent_node_token):
            title = _n(node.get("title", ""))
            if not title:
                continue
            if (needle in title or title in needle) and len(title) > best_len:
                best_token, best_len = node.get("node_token", ""), len(title)
        return best_token

    def list_nodes(self, space_id: str, parent_node_token: str = "") -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = ""
        while True:
            params: Dict[str, Any] = {"page_size": 50}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page:
                params["page_token"] = page
            payload = self._get(f"/wiki/v2/spaces/{space_id}/nodes", params=params)
            data = payload.get("data", {})
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page = data.get("page_token", "")
        return items

    def find_doc_urls(self, product_name: str, build: str, titles: List[str]) -> Dict[str, Dict[str, str]]:
        """在“项目+版本”目录下按标题检索文档，返回 {标题: {"title": 节点真实名, "url": 知识库链接}}（未命中则该项缺失）。

        严格限定作用域以防跨版本/跨项目张冠李戴：
        产品名→同名产品子节点（模糊包含，与推送落点一致）→版本号→**精确同名**版本目录（只找不建）；
        任一环节缺失即返回空，**绝不退而搜父层/其他版本**。仅拼 node_token 生成链接，不读文档正文。
        """
        f = self.f
        out: Dict[str, Dict[str, str]] = {}
        if not (product_name and build and f.wiki_space_id and f.wiki_projects_parent):
            return out
        project_node = self.find_child_by_title(f.wiki_space_id, f.wiki_projects_parent, product_name)
        if not project_node:
            return out
        version_node = self.find_child_node(f.wiki_space_id, project_node, build)
        if not version_node:  # 未找到该版本目录→不向下猜，直接空（避免拿到别的版本）
            return out
        nodes = self.list_nodes(f.wiki_space_id, version_node)
        for want in titles:
            node = self._match_child_node(nodes, want)
            if node and node.get("node_token"):
                out[want] = {
                    "title": str(node.get("title") or want).strip(),
                    "url": f"{f.doc_base_url.rstrip('/')}/wiki/{node.get('node_token', '')}",
                }
        return out

    @staticmethod
    def _match_child_node(nodes: List[Dict[str, Any]], title: str, obj_type: str = "") -> Dict[str, Any]:
        """在节点列表中找标题**包含**目标名的文档节点，返回该节点 dict（无则空 dict）。

        obj_type 非空时仅匹配该类型节点（如 `file`）：下载用例表场景须精确锁定上传的 xlsx 附件，
        跳过同目录下思维导图 mindnote / docx 等同名异类型节点。
        """
        def _n(s: str) -> str:
            s = str(s or "").lower()
            for ch in "“”\"'  \t\u3000《》":
                s = s.replace(ch, "")
            return s

        needle = _n(title)
        if not needle:
            return {}
        for node in nodes:
            if obj_type and str(node.get("obj_type", "")) != obj_type:
                continue
            if needle in _n(node.get("title", "")):
                return node
        return {}

    # ---- 版本目录节点定位（只读，供 V2-4 测试范围接地下载用例表）----
    def find_doc_obj_token(self, product_name: str, build: str, title: str, obj_type: str = "") -> str:
        """在「项目+版本」目录下按标题定位节点，返回其 obj_token。

        obj_type 控制只匹配哪类节点（如 `file` 精确锁定上传的用例 xlsx，避免误抓同名 docx/mindnote）；
        传空串则不限类型。沿用 find_doc_urls 的严格作用域以防跨项目/跨版本张冠李戴：
        产品名→同名产品子节点（模糊包含）→版本号→**精确同名**版本目录（只找不建）→标题包含；
        任一环节缺失即返回空，**绝不退而搜父层/其他版本**。
        """
        f = self.f
        if not (product_name and build and f.wiki_space_id and f.wiki_projects_parent):
            return ""
        project_node = self.find_child_by_title(f.wiki_space_id, f.wiki_projects_parent, product_name)
        if not project_node:
            return ""
        version_node = self.find_child_node(f.wiki_space_id, project_node, build)
        if not version_node:  # 未找到该版本目录→不向下猜
            return ""
        node = self._match_child_node(self.list_nodes(f.wiki_space_id, version_node), title, obj_type=obj_type)
        return str(node.get("obj_token", "") or "")
