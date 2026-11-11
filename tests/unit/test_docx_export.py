"""md→docx 内嵌图表转换 与 push_feishu 导入格式选择 的单元测试（不联网）。

- md_to_docx_bytes：pandoc 可用时把相对路径图片内嵌进 docx（word/media/）；不可用时返回 None。
- push_feishu：优先走 docx（内嵌图），转换失败/关闭开关时回退 md；用桩客户端避免联网。
"""
import base64
import io
import re
import zipfile

import pytest

from report_agent.config import load_config
from report_agent.report import docx_export
from report_agent.report.docx_export import md_to_docx_bytes, pandoc_available
from report_agent.tools import push_feishu
from report_agent.tools.push_feishu import PushFeishuTool

# 1x1 PNG（可被 pandoc 解码并内嵌）
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

BASE = {"llm.use_llm_planner": False, "zentao.mode": "mock", "trace.enabled": False}


def _cfg(**over):
    return load_config(**{**BASE, "feishu.enabled": True, "feishu.app_id": "cli_x",
                          "feishu.app_secret": "s", "feishu.upload_folder_token": "fld",
                          "feishu.target": "drive", **over})


@pytest.mark.skipif(not pandoc_available(), reason="需要 pandoc")
def test_md_to_docx_embeds_local_image(tmp_path):
    (tmp_path / "img.png").write_bytes(base64.b64decode(_PNG_B64))
    md = "# 报告\n\n![图](img.png)\n"
    docx_bytes = md_to_docx_bytes(md, tmp_path)
    assert docx_bytes and docx_bytes[:2] == b"PK"  # docx=zip
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        assert any(n.startswith("word/media/") for n in z.namelist())


def test_md_to_docx_returns_none_without_pandoc(monkeypatch, tmp_path):
    monkeypatch.setattr(docx_export.shutil, "which", lambda name: None)
    assert md_to_docx_bytes("# x\n", tmp_path) is None


def _cx(docx_bytes):
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    return int(re.search(r'<wp:extent cx="(\d+)"', xml).group(1))


@pytest.mark.skipif(not pandoc_available(), reason="需要 pandoc")
def test_scale_image_extents(tmp_path):
    (tmp_path / "img.png").write_bytes(base64.b64decode(_PNG_B64))
    raw = md_to_docx_bytes("![](img.png)\n", tmp_path, image_scale=1.0)
    scaled = md_to_docx_bytes("![](img.png)\n", tmp_path, image_scale=1.6)
    assert _cx(scaled) > _cx(raw)
    assert _cx(scaled) == round(_cx(raw) * 1.6)


def test_scale_noop_when_one():
    # scale=1.0 时直接返回原字节，不解包/不改写
    data = b"not-a-zip"
    assert docx_export._scale_image_extents(data, 1.0) is data


_TR_HEADER = ('<w:tr><w:trPr><w:tblHeader w:val="on" /></w:trPr>'
              '<w:tc><w:tcPr /><w:p><w:r><w:rPr><w:rFonts w:hint="eastAsia" /></w:rPr><w:t>模块</w:t></w:r></w:p></w:tc>'
              '<w:tc><w:tcPr /><w:p><w:r><w:t>描述</w:t></w:r></w:p></w:tc></w:tr>')
_TR_DATA = ('<w:tr><w:tc><w:tcPr /><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc></w:tr>')


def test_style_document_xml_header_only():
    """表头行：底纹+加粗（无 rPr 的 run 补造 rPr）；数据行原样不动；重复执行幂等。"""
    out = docx_export._style_document_xml(_TR_HEADER + _TR_DATA)
    assert out.count('w:fill="DEEAF6"') == 2                      # 两个表头单元格上底色
    assert '<w:rFonts w:hint="eastAsia" /><w:b /></w:rPr>' in out  # 已有 rPr：b 追加在 rFonts 后
    assert '<w:r><w:rPr><w:b /></w:rPr><w:t>描述</w:t>' in out     # 无 rPr：补造加粗 run
    assert _TR_DATA in out                                          # 数据行未被触碰
    assert docx_export._style_document_xml(out) == out              # 幂等


@pytest.mark.skipif(not pandoc_available(), reason="需要 pandoc")
def test_md_to_docx_styles_table_header(tmp_path):
    md = "| 模块 | 描述 |\n| --- | --- |\n| A | B |\n"
    with zipfile.ZipFile(io.BytesIO(md_to_docx_bytes(md, tmp_path))) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    head, _, rest = xml.partition("</w:tr>")   # 首行=表头
    assert 'w:fill="DEEAF6"' in head and "<w:b />" in head
    assert 'w:fill="DEEAF6"' not in rest and "<w:b />" not in rest  # 数据行无样式


class _StubClient:
    def __init__(self, f):
        self.upload = None
        self.import_ext = None

    def get_tenant_token(self):
        return "t"

    def upload_bytes(self, name, content, folder_token, mime="text/markdown"):
        self.upload = (name, mime)
        return "FT"

    def create_import_task(self, file_token, name, folder_token, file_extension="md"):
        self.import_ext = file_extension
        return "TK"

    def poll_import_result(self, ticket):
        return {"token": "DOC1"}


@pytest.fixture
def stub_client(monkeypatch):
    holder = {}

    def _factory(f):
        holder["c"] = _StubClient(f)
        return holder["c"]

    monkeypatch.setattr(push_feishu, "FeishuClient", _factory)
    return holder


def test_push_uses_docx_when_conversion_ok(monkeypatch, stub_client):
    monkeypatch.setattr(push_feishu, "md_to_docx_bytes", lambda md, rd, **k: b"DOCXBYTES")
    res = PushFeishuTool(_cfg()).run(report_markdown="# x\n", version_name="UniResearch V5.0.0")
    assert res.ok and res.meta["import_format"] == "docx"
    assert stub_client["c"].upload[0].endswith(".docx")
    assert "wordprocessingml" in stub_client["c"].upload[1]
    assert stub_client["c"].import_ext == "docx"
    assert res.data.endswith("/docx/DOC1")


def test_push_falls_back_to_md_when_conversion_fails(monkeypatch, stub_client):
    monkeypatch.setattr(push_feishu, "md_to_docx_bytes", lambda md, rd, **k: None)
    res = PushFeishuTool(_cfg()).run(report_markdown="# x\n", version_name="UniResearch V5.0.0")
    assert res.ok and res.meta["import_format"] == "md"
    assert stub_client["c"].upload[0].endswith(".md")
    assert stub_client["c"].import_ext == "md"


def test_push_skips_conversion_when_switch_off(monkeypatch, stub_client):
    called = {"n": 0}

    def _boom(md, rd, **k):
        called["n"] += 1
        raise AssertionError("关闭开关时不应调用转换")

    monkeypatch.setattr(push_feishu, "md_to_docx_bytes", _boom)
    res = PushFeishuTool(_cfg(**{"feishu.import_as_docx": False})).run(
        report_markdown="# x\n", version_name="UniResearch V5.0.0")
    assert res.ok and called["n"] == 0 and res.meta["import_format"] == "md"
