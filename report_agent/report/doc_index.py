"""§1.3 手册/UI/原型链接接地（V2 延伸）：从飞书知识库「项目层」链接汇总文档逐字提取外链。

背景：软件用户手册 / UI 设计文档 / 产品原型文档在项目内各版本间恒定，团队已把它们
汇总为一篇表格文档（如 UniResearch项目测试 → UniResearch 相关账号及说明文档 →
Uniresearch原型、UI设计、用户手册链接，正文表格即「文档名 | 链接」行组，链接可能是
明文 URL 或带 hyperlink 的显示文字）。本模块沿用 2.2 环境接地同一套项目层检索与
「只抄不写」口径：逐字引用块内 URL，任一校验不过该行不填，绝不猜测。

红线（对齐 find_doc_urls 逐层收窄不兜底不变量）：
- 只在项目节点层检索候选（复用 test_env.collect_env_candidates，深度 ≤2、跳过版本目录）；
- 文档名须与表行名去空白后全等，其后相邻文本块须给出 http(s) URL（明文或 link.url 解码），
  形态不符即该块不算命中——宁缺毋滥，不做模糊配对；
- 单源采纳（第一份解析出 ≥1 条的文档整体采用，不跨文档合并）；
- 全部未命中/任何异常 → 返回 {}，1.3 相应行维持记忆/人工占位；开关关闭时零触碰网络。
"""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import unquote

# 1.3 表中由本模块负责的三行（与 templates.REFERENCE_DOC_ROWS 行名一致，比对时去空白）。
DOC_INDEX_ROW_NAMES = ("软件用户手册", "UI 设计文档", "产品原型文档")

_CANONICAL = {"".join(n.split()): n for n in DOC_INDEX_ROW_NAMES}


def _block_text(block: Dict[str, Any]) -> str:
    """block 内全部 text_run 的显示文本（拼接）。"""
    return "".join(str(el.get("text_run", {}).get("content", ""))
                   for el in block.get("text", {}).get("elements", []) or [])


def _block_url(block: Dict[str, Any]) -> str:
    """取 block 中的可用外链：明文 http(s) 内容优先，其次 hyperlink 的 link.url（百分号编码需解码）。

    只认解码后仍以 http(s):// 开头的值；其余形态（纯文字/相对路径）一律视为无链接。
    """
    for el in block.get("text", {}).get("elements", []) or []:
        run = el.get("text_run") or {}
        content = str(run.get("content", "")).strip()
        if content.lower().startswith(("http://", "https://")):
            return content
        link_url = str((run.get("text_element_style") or {}).get("link", {}).get("url", "") or "")
        decoded = unquote(link_url).strip()
        if decoded.lower().startswith(("http://", "https://")):
            return decoded
    return ""


def parse_doc_link_blocks(blocks: List[Dict[str, Any]]) -> Dict[str, str]:
    """解析 docx blocks 为 {表行名: url}：文档名块与链接块须相邻成对，缺一不填。

    遍历顺序即文档正文顺序（含表格单元格文本块），命中条件（全部满足才采纳）：
    1) 当前文本块去空白后与 DOC_INDEX_ROW_NAMES 某行全等；
    2) 紧邻的下一个文本块能给出 http(s) URL（_block_url 口径）。
    """
    texts = [b for b in blocks or [] if b.get("block_type") == 2]
    out: Dict[str, str] = {}
    for i, block in enumerate(texts):
        canon = _CANONICAL.get("".join(_block_text(block).split()))
        if not canon or canon in out:
            continue
        url = _block_url(texts[i + 1]) if i + 1 < len(texts) else ""
        if url:
            out[canon] = url
    return out


def resolve_project_doc_index(client, product_name: str, keywords: List[str]) -> Dict[str, str]:
    """逐份候选读 blocks、解析行组；第一份解析出 ≥1 条的即采纳（单源采纳）。

    候选检索复用 test_env.collect_env_candidates（项目层、深度 ≤2、跳过版本目录）。
    返回 {"软件用户手册": url, …}（可能不完整，缺的行由上层维持记忆/人工占位）；
    全部未命中/任何异常返回 {}。
    """
    try:
        from .test_env import collect_env_candidates

        for node in collect_env_candidates(client, product_name, keywords):
            blocks = client.read_doc_blocks(str(node.get("obj_token", "")))
            hits = parse_doc_link_blocks(blocks)
            if hits:
                return hits
    except Exception:
        return {}
    return {}
