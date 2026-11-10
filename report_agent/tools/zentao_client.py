"""禅道 RESTful API 客户端（/api.php/v1）。

职责：
- 认证：账号密码换 token（POST /tokens），或直接使用预置只读 token
- 只读 GET 请求 + 分页拉取
- 超时/连接错误/非 2xx 统一抛 ZentaoError，由上层工具转为 fetch_status

注意（F7 只读原则）：本客户端只提供 GET，不提供任何写操作。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class ZentaoError(Exception):
    """禅道访问异常。kind: timeout | error | auth | not_found"""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def _extract_list(payload: Any, keys: List[str]) -> List[Dict[str, Any]]:
    """禅道不同版本返回结构不一：可能是 list，或 {"bugs":[...]} / {"products":[...]} / {"data":[...]}。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        # 兜底：取第一个 list 型字段
        for v in payload.values():
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


# 版本号 token：优先带 V 前缀（V5.0.0 / V6.6.7.2），其次裸多段号（5.0.0 / 6.6.7.2）；支持任意段数
_BUILD_RE = re.compile(r"[Vv]\d+(?:\.\d+)+")
_BUILD_RE_LOOSE = re.compile(r"\d+(?:\.\d+)+")


def parse_version(version_name: str):
    """把用户输入的「产品名 + 版本号」拆为 (product_keyword, build)。

    禅道把“版本”建模为缺陷的 openedBuild，产品名为 UniResearch；因此需拆开：
    'UniResearch V5.0.0' -> ('UniResearch', 'V5.0.0')；无版本 token 时 build=None。
    """
    s = (version_name or "").strip()
    m = _BUILD_RE.search(s) or _BUILD_RE_LOOSE.search(s)
    if not m:
        return s, None
    build = m.group()
    keyword = (s[: m.start()] + " " + s[m.end():]).strip()
    return keyword, build


def build_matches(value: Any, build: Optional[str]) -> bool:
    """缺陷的影响版本字段是否包含指定版本（逗号分隔多版本，“包含即计入”，忽略大小写）。

    build 为 None 时不过滤（返回 True）。
    """
    if build is None:
        return True
    target = build.strip().lower()
    if isinstance(value, list):
        tokens = [str(v) for v in value]
    elif value is None:
        tokens = []
    else:
        tokens = str(value).split(",")
    return any(t.strip().lower() == target for t in tokens)


class ZentaoRestClient:
    def __init__(self, cfg) -> None:
        self.z = cfg.zentao
        self.timeout = self.z.timeout_seconds
        self._token: Optional[str] = self.z.token or None

    # ---- 内部 ----
    def _base(self) -> str:
        return self.z.base_url.rstrip("/") + self.z.api_prefix

    def _requests(self):
        try:
            import requests
            return requests
        except ImportError as exc:
            raise ZentaoError("缺少 requests 依赖，请先 pip install -r requirements.txt", "error") from exc

    def authenticate(self) -> str:
        if self._token:
            return self._token
        if not self.z.account or not self.z.password:
            raise ZentaoError("未配置禅道账号密码或只读 token（检查 .env 的 ZENTAO_ACCOUNT/ZENTAO_PASSWORD 或 ZENTAO_TOKEN）", "auth")
        requests = self._requests()
        url = self._base() + "/tokens"
        try:
            resp = requests.post(
                url,
                json={"account": self.z.account, "password": self.z.password},
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise ZentaoError(f"禅道认证超时：{url}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise ZentaoError(f"禅道认证连接失败：{exc}", "error") from exc
        if resp.status_code in (401, 403):
            raise ZentaoError("禅道认证失败：账号或密码错误（401/403）", "auth")
        if resp.status_code == 404:
            raise ZentaoError(f"禅道 REST 接口不存在（404）：{url}；该禅道可能未开启 /api.php/v1，请改用传统接口或数据库直连", "not_found")
        if resp.status_code >= 400:
            raise ZentaoError(f"禅道认证返回 {resp.status_code}", "error")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ZentaoError("禅道认证返回非 JSON，可能不是 RESTful API", "error") from exc
        self._token = data.get("token")
        if not self._token:
            raise ZentaoError("禅道认证未返回 token", "auth")
        return self._token

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        requests = self._requests()
        token = self.authenticate()
        url = self._base() + path
        try:
            resp = requests.get(url, headers={"Token": token}, params=params or {}, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise ZentaoError(f"禅道接口超时：{url}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise ZentaoError(f"禅道接口连接失败：{exc}", "error") from exc
        if resp.status_code == 404:
            raise ZentaoError(f"禅道接口不存在（404）：{url}", "not_found")
        if resp.status_code >= 400:
            raise ZentaoError(f"禅道接口返回 {resp.status_code}：{url}", "error")
        try:
            return resp.json()
        except ValueError as exc:
            raise ZentaoError("禅道接口返回非 JSON", "error") from exc

    # ---- 业务查询 ----
    def find_product(self, keyword: str) -> Optional[Dict[str, Any]]:
        """按产品名关键字匹配 product：先精确（忽略大小写），再包含/前缀。分页遍历。"""
        name_field = self.z.field_product_name
        target = (keyword or "").strip().lower()
        page = 1
        contains_hit: Optional[Dict[str, Any]] = None
        while True:
            payload = self.get("/products", params={"limit": self.z.page_size, "page": page})
            items = _extract_list(payload, ["products", "data"])
            if not items:
                break
            for p in items:
                nm = str(p.get(name_field, "")).strip().lower()
                if nm == target:
                    return p
                if contains_hit is None and target and (target in nm or nm in target):
                    contains_hit = p
            if len(items) < self.z.page_size:
                break
            page += 1
        return contains_hit

    def get_product_bugs(self, product_id: Any) -> List[Dict[str, Any]]:
        """拉取某 product 的全部缺陷（分页）。

        必须带 status=all：禅道 REST 默认视图只返回未关闭缺陷（active/resolved），
        不传则已关闭(closed)全部缺失，会导致缺陷总数/修复覆盖率严重漏算（实测 product17
        默认 240 vs status=all 4376）。只读（F7）：仅 GET。
        """
        all_bugs: List[Dict[str, Any]] = []
        page = 1
        while True:
            payload = self.get(f"/products/{product_id}/bugs", params={"status": "all", "limit": self.z.page_size, "page": page})
            items = _extract_list(payload, ["bugs", "data"])
            all_bugs.extend(items)
            total = payload.get("total") if isinstance(payload, dict) else None
            if total is not None and len(all_bugs) >= int(total):
                break
            if len(items) < self.z.page_size:
                break
            page += 1
        return all_bugs

    def get_bug(self, bug_id: Any) -> Optional[Dict[str, Any]]:
        """取单条缺陷详情（含列表接口缺失的 moduleTitle 等）。失败返回 None（不阻断主流程）。"""
        if bug_id is None:
            return None
        try:
            data = self.get(f"/bugs/{bug_id}")
        except ZentaoError:
            return None
        if isinstance(data, dict) and isinstance(data.get("bug"), dict):
            return data["bug"]
        return data if isinstance(data, dict) else None
