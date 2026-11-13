"""2.2 测试环境接地（V2 延伸）：从飞书知识库「项目层」环境文档逐字提取地址与账号。

背景：环境地址/账号密码在同一项目的各版本间恒定，团队已把它们维护在项目目录下
（如 UniResearch项目测试 → UniResearch 相关账号及说明文档 → Uniresearch测试环境及账号，
正文即「环境名 / URL / 账号密码」三段式行组）。本模块把 2.2 从纯人工占位升级为
「只抄不写」的接地填充：逐字引用原文，任一校验不过整体回退占位，绝不编造凭证。

安全口径（用户 2026-11-13 拍板）：凭证逐字写入报告 2.2，表后不另加来源标注文字；
报告与源文档同处知识库权限体系，敏感度不升级。

红线（对齐 find_doc_urls 逐层收窄不兜底不变量）：
- 只在项目节点层检索（标题含关键词的 docx），**跳过版本目录不下探**，深度 ≤2；
- 行组须「环境名 + http(s) 地址 + 非空账号」三件齐备才算命中，缺任一件该行不填；
- 全部未命中/任何异常 → 返回 None，渲染层保持人工占位；开关关闭时零触碰网络。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# 2.2 表的行名（与 templates.TEST_ENV_TABLE 行一致），逐字比对用。
ENV_ROW_NAMES = ("测试环境", "预发布环境", "生产环境")

# 项目节点下视为版本目录、不下探不匹配的标题（V5.0.0 / 0.0.2 等）。
_VERSION_DIR_RE = re.compile(r"^[Vv]?\d+(?:\.\d+)*$")


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split()).strip()


def parse_env_rows(text: str) -> Dict[str, Dict[str, str]]:
    """把环境文档正文解析为 {行名: {"url":…, "creds":…}}；只认逐字行组，解析不出即空。

    形态（实测 UniResearch）：每行一个字段，顺序为 环境名 → URL → 账号密码；
    环境名须与 ENV_ROW_NAMES 全等（去空白后），URL 须以 http(s):// 开头，
    凭证行非空且不是下一个环境名。不满足该形态的文档一律解析为空（回退占位），
    不做模糊猜测式提取——宁缺毋滥。
    """
    lines = [_norm(l) for l in str(text or "").splitlines()]
    lines = [l for l in lines if l]
    rows: Dict[str, Dict[str, str]] = {}
    for i, line in enumerate(lines):
        if line not in ENV_ROW_NAMES or line in rows:
            continue
        url = lines[i + 1] if i + 1 < len(lines) else ""
        creds = lines[i + 2] if i + 2 < len(lines) else ""
        if not url.lower().startswith(("http://", "https://")):
            continue
        if not creds or creds in ENV_ROW_NAMES:
            continue
        rows[line] = {"url": url, "creds": creds}
    return rows


def render_env_table(rows: Dict[str, Dict[str, str]], source_title: str = "") -> str:
    """按 ENV_ROW_NAMES 顺序渲染 2.2 表：命中行逐字填原文，未命中行保持【待补充】。

    source_title 仅作调用侧溯源记录用，不写入报告正文（用户 2026-11-13 拍板：表后不加来源标注文字）。
    """
    out = ["| 环境 | 测试地址 | 账号密码 |", "| ---------- | ------------------------- | ------------------------- |"]
    for name in ENV_ROW_NAMES:
        hit = rows.get(name)
        if hit:
            out.append(f"| {name} | {hit['url']} | {hit['creds']} |")
        else:
            out.append(f"| {name} | 【待补充】 | 【待补充】 |")
    return "\n".join(out)


def _is_candidate(node: Dict[str, Any], keywords: List[str]) -> bool:
    """项目层候选节点：docx、标题含任一关键词、且不是版本目录。"""
    if str(node.get("obj_type", "")) != "docx":
        return False
    title = _norm(node.get("title", ""))
    if not title or _VERSION_DIR_RE.match(title):
        return False
    low = title.lower()
    return any(str(k).lower() in low for k in keywords if _norm(k))


def collect_env_candidates(client, product_name: str, keywords: List[str]) -> List[Dict[str, Any]]:
    """在项目节点层收集环境文档候选（深度 ≤2，跳过版本目录，逐层收窄不兜底）。"""
    f = getattr(client, "f", None)
    if not (product_name and f is not None and f.wiki_space_id and f.wiki_projects_parent):
        return []
    project_node = client.find_child_by_title(f.wiki_space_id, f.wiki_projects_parent, product_name)
    if not project_node:
        return []
    direct = client.list_nodes(f.wiki_space_id, project_node)
    cands = [n for n in direct if _is_candidate(n, keywords)]
    # 第二层：项目直属节点的子节点（如「相关账号及说明文档」下的环境文档）；版本目录不下探。
    # 自身命中关键词的父节点也要下探：父文档可能只是目录性说明，真表常在子文档。
    for n in direct:
        if _VERSION_DIR_RE.match(_norm(n.get("title", ""))) or not n.get("has_child"):
            continue
        for child in client.list_nodes(f.wiki_space_id, str(n.get("node_token", ""))):
            if _is_candidate(child, keywords):
                cands.append(child)
    return cands


def resolve_test_env(client, product_name: str, keywords: List[str]) -> Optional[Dict[str, Any]]:
    """逐份候选读正文、解析行组；第一份解析出 ≥1 行的即采纳（单源采纳，不跨文档合并）。

    返回 {"rows":…, "source_title":…, "url":知识库链接}；全部未命中/任何异常返回 None（回退占位）。
    """
    f = getattr(client, "f", None)
    try:
        for node in collect_env_candidates(client, product_name, keywords):
            text = client.read_doc_raw_content(str(node.get("obj_token", "")))
            rows = parse_env_rows(text)
            if rows:
                title = _norm(node.get("title", ""))
                url = f"{f.doc_base_url.rstrip('/')}/wiki/{node.get('node_token', '')}"
                return {"rows": rows, "source_title": title, "url": url}
    except Exception:
        return None
    return None
