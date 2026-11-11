"""Markdown → docx 本地转换（pandoc）。

用于飞书推送：飞书导入服务在云端解析 Markdown，读不到本机相对路径的图片，
图表会被丢弃。改由本地 pandoc 把报告转成 docx（图片内嵌进 word/media/），
再上传导入 docx，图表即可随文件进入飞书。

设计取舍：pandoc 缺失或转换失败时返回 None，调用方回退为直接导入 Markdown
（文字/表格照常、仅图丢失），不阻断推送。
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional


def _apply_document_xml(docx_bytes: bytes, fn) -> bytes:
    """读出 word/document.xml 交 fn 改写后重打包；无该条目或字节为空时原样返回。"""
    if not docx_bytes:
        return docx_bytes
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zin:
        items = {n.filename: zin.read(n.filename) for n in zin.infolist()}
    xml = items.get("word/document.xml")
    if xml is None:
        return docx_bytes
    items["word/document.xml"] = fn(xml.decode("utf-8")).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    return buf.getvalue()


def _scale_image_extents(docx_bytes: bytes, scale: float) -> bytes:
    """把 docx 里所有图片的显示尺寸（wp:extent / a:ext 的 cx、cy）按 scale 等比放大。

    只改图片尺寸，不动页面设置与正文/表格。scale=1.0 时原样返回（不解包）。
    最终显示大小仍受飞书正文宽度上限约束（但高于 pandoc 默认的 5.83in，故会变大）。
    """
    if scale == 1.0 or not docx_bytes:
        return docx_bytes

    def _scale_xml(xml_text: str) -> str:
        def _repl(m: "re.Match[str]") -> str:
            # 分组：1=`<wp:extent cx="`  2=cx数字  3=`" cy="`  4=cy数字
            cx, cy = int(m.group(2)), int(m.group(4))
            return f'{m.group(1)}{round(cx * scale)}{m.group(3)}{round(cy * scale)}'
        # 匹配 <wp:extent cx="N" cy="M" 与 <a:ext cx="N" cy="M（两组属性名顺序一致）
        return re.sub(r'(<(?:wp:extent|a:ext) cx=")(\d+)(" cy=")(\d+)', _repl, xml_text)

    return _apply_document_xml(docx_bytes, _scale_xml)


# 表头浅蓝底（Word 标准色「蓝色, 着色1, 淡色80%」）。
_HEADER_FILL = "DEEAF6"


def _style_document_xml(xml_text: str) -> str:
    """给所有表格的首行（pandoc 以 <w:tblHeader/> 标记）单元格加浅蓝底纹、文字加粗。

    只动表头行；数据行保持原样。tcPr 兼容自闭合（pandoc 默认）与非自闭合两种写法；
    已有 <w:b> 的 run 不重复注入（幂等）。
    """
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{_HEADER_FILL}" />'

    def _bold_rpr(m: "re.Match[str]") -> str:
        block = m.group(0)
        if "<w:b " in block or "<w:b>" in block or "<w:b/" in block:
            return block
        return block.replace("</w:rPr>", "<w:b /></w:rPr>")

    def _style_row(m: "re.Match[str]") -> str:
        tr = m.group(0)
        if "<w:tblHeader" not in tr:  # 非表头行原样返回
            return tr
        tr = re.sub(r"<w:tcPr\s*/>", f"<w:tcPr>{shd}</w:tcPr>", tr)
        tr = re.sub(r"(<w:tcPr>)(.*?)(</w:tcPr>)",
                    lambda c: c.group(1) + c.group(2) + shd + c.group(3)
                    if "<w:shd" not in c.group(2) else c.group(0),
                    tr, flags=re.S)
        tr = re.sub(r"<w:rPr>.*?</w:rPr>", _bold_rpr, tr, flags=re.S)
        tr = re.sub(r"(<w:r(?:\s[^>]*)?>)(?!<w:rPr)", r"\1<w:rPr><w:b /></w:rPr>", tr)
        return tr

    return re.sub(r"<w:tr(?:\s[^>]*)?>.*?</w:tr>", _style_row, xml_text, flags=re.S)


def _style_table_headers(docx_bytes: bytes) -> bytes:
    """表头加粗+浅蓝底纹的 docx 后处理入口（见 _style_document_xml）。"""
    return _apply_document_xml(docx_bytes, _style_document_xml)


def pandoc_available() -> bool:
    """PATH 中是否可用 pandoc（不引入 pypandoc，直接调用系统 pandoc）。"""
    return shutil.which("pandoc") is not None


def md_to_docx_bytes(markdown: str, resource_dir: Path | str, timeout: int = 60,
                     image_scale: float = 1.0) -> Optional[bytes]:
    """把 Markdown 文本转成 docx 字节。

    相对图片路径（如 ``assets/<版本>/xxx.png``）按 ``resource_dir`` 解析——
    即报告输出目录（cfg.report_dir），图片就在其下 assets/。
    image_scale>1 时等比放大图片在文档中的显示尺寸（仅图，不改页面）。
    pandoc 不可用、执行失败或无产物时返回 None（调用方回退 md）。
    """
    if not pandoc_available():
        return None
    resource_dir = Path(resource_dir)
    with tempfile.TemporaryDirectory() as td:
        md_path = Path(td) / "report.md"
        docx_path = Path(td) / "report.docx"
        md_path.write_text(markdown, encoding="utf-8")
        cmd = [
            "pandoc", str(md_path),
            "-f", "markdown", "-t", "docx",
            "--resource-path", str(resource_dir),
            "-o", str(docx_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError):
            return None
        if not docx_path.exists():
            return None
        return _scale_image_extents(_style_table_headers(docx_path.read_bytes()), image_scale)
