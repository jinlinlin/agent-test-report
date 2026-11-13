"""项目级参考文档链接记忆（V2-3 第 1 步：只做存/取，不碰渲染）。

背景：软件用户手册 / UI 设计文档 / 产品原型文档 这类参考文档链接，
同一项目的每个版本都一样，但飞书「项目+版本」目录里不一定能自动定位到。
（需求文档不在此列：与测试大纲/用例同走版本目录检索，见 templates.REF_DOC_LINK_TITLES。）
本模块提供一个本地 JSON 小抄：某项目某类型的链接一旦被确认提供，就按「项目」登记，
之后同项目直接复用；新项目 / 新类型没有记录则返回空，由上层决定是否首次询问用户。

安全红线（对齐用户「复用一定要是正确的」）：
- 严格按项目隔离：key 归一化到小写、压缩空白，同项目不同写法落到同一格，不同项目互不串；
  简称→全名这类语义归一由上层 planner 负责，本模块只认传入的规范项目名。
- 新项目 / 新类型无记录一律返回 None，绝不跨项目借用、不设全局默认值兜底。
- 登记前校验 URL 合法性（仅接受 http/https），非法一律不入库，杜绝把乱码当链接记住。
- 命中即覆盖旧值：飞书里改了地址，只要再提供一次即可纠正。
- 纯本地读写，不发任何网络请求；文件缺失 / 损坏一律降级为空 dict，绝不抛断上层流程。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# 参考文档中「同一项目每版本都一样、适合按项目记忆」的类型（需求文档已改走检索，不属记忆组）。
PROJECT_DOC_TYPES = ("软件用户手册", "UI 设计文档", "产品原型文档")


def _norm_project(product: str) -> str:
    """项目名归一化：去首尾空白、压缩内部连续空白、转小写，用于把同项目不同写法收敛到一格。"""
    return " ".join(str(product or "").split()).strip().lower()


def _is_valid_url(url: str) -> bool:
    """仅接受带主机名的 http/https 链接（飞书文档链接均为 https）。"""
    u = str(url or "").strip()
    if not u:
        return False
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class DocLinkStore:
    """本地项目级文档链接小抄：load / register / lookup / all_for_project。"""

    def __init__(self, path: "Path | str") -> None:
        self.path = Path(path)
        self._data: Dict[str, Any] = self._load()

    # ---- 持久化 ----
    def _load(self) -> Dict[str, Any]:
        try:
            if not self.path.exists():
                return {}
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (ValueError, OSError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 原子替换，避免半写文件

    # ---- 查询 ----
    def lookup(self, product: str, doc_type: str) -> Optional[Dict[str, str]]:
        """命中且 URL 合法则返回 {url, title}；项目/类型缺失或记录损坏返回 None（交上层决定是否询问）。"""
        proj = self._data.get(_norm_project(product))
        if not isinstance(proj, dict):
            return None
        entry = proj.get(str(doc_type or "").strip())
        if isinstance(entry, dict) and _is_valid_url(entry.get("url", "")):
            title = str(entry.get("title") or doc_type).strip() or str(doc_type).strip()
            return {"url": str(entry["url"]).strip(), "title": title}
        return None

    def all_for_project(self, product: str) -> Dict[str, Dict[str, str]]:
        """返回该项目已登记的全部 {类型: {url, title, updated_at}}（仅合法条目），供展示/核对。"""
        proj = self._data.get(_norm_project(product))
        if not isinstance(proj, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for dtype, entry in proj.items():
            if isinstance(entry, dict) and _is_valid_url(entry.get("url", "")):
                out[dtype] = {
                    "url": str(entry["url"]).strip(),
                    "title": str(entry.get("title") or dtype).strip() or dtype,
                    "updated_at": str(entry.get("updated_at") or ""),
                }
        return out

    # ---- 登记 ----
    def register(self, product: str, doc_type: str, url: str, title: str = "") -> bool:
        """登记 / 覆盖一条链接。项目名或类型为空、URL 非法则不写并返回 False（防把乱码记成链接）。"""
        url = str(url or "").strip()
        dtype = str(doc_type or "").strip()
        if not str(product or "").strip() or not dtype or not _is_valid_url(url):
            return False
        proj = self._data.setdefault(_norm_project(product), {})
        proj[dtype] = {
            "url": url,
            "title": str(title or "").strip() or dtype,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._persist()
        return True
