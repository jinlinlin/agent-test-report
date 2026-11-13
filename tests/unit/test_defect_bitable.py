"""第 6 节缺陷清单（飞书多维表格）的单元测试（不联网）。

覆盖：
- bug_to_fields：枚举中文化、重现步骤全文不截断、空单选留 None、日期格式化；
- render_report 第 6 节：inline=True→只出计数引导；否则回退正文宽表（B 独立节点已废弃，不再产链接）；
- C 内嵌：FeishuClient.embed_defect_bitable 编排（每组各插一块/清理默认列行/建列/改名/灌记录）；
  RenderReportTool._defect_embed_mode 门控；PushFeishuTool._embed_defects 门控与成功返回。
"""
from report_agent.config import load_config
from report_agent.report.renderer import (
    DEFECT_FIELD_SPECS,
    bug_to_fields,
    render_report,
)
from report_agent.schemas.contracts import BugRow
from report_agent.tools import render_report as rr
from report_agent.tools.feishu_client import FeishuClient
from report_agent.tools.statistics import compute_statistics

BASE = {"llm.use_native_fc": False, "zentao.mode": "mock", "trace.enabled": False}


def _stats(bugs):
    return compute_statistics({"name": "UniResearch V5.1.0", "build": "V5.1.0", "fetch_status": "ok"}, bugs)


def test_bug_to_fields_full_and_mapped():
    row = BugRow(id=9, module="/M", title="标题", priority=2, type="codeerror",
                 steps="<p>" + "很长的重现步骤" * 40 + "</p>", status="active",
                 opened_by="张三", opened_date="2026-11-09T02:01:28Z", opened_build="V5.1.0",
                 assigned_to="李四", resolution="")
    f = bug_to_fields(row, 1)
    assert f["序号"] == "1" and f["Bug编号"] == "9"
    assert f["功能类型"] == "代码错误" and f["bug状态"] == "激活"
    assert f["解决方案"] is None          # 空单选不建空选项
    assert f["创建日期"] == "2026-11-09"
    assert "<p>" not in f["重现步骤"]      # HTML 去标签
    assert len(f["重现步骤"]) > 120        # 全文不截断（区别于内嵌表）


def test_section6_inline_when_no_url():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    md = render_report(stats)
    # 空类不展示：postponed 为空 → 无延期小节，激活类顺次重编号为 6.1
    assert "延期处理缺陷清单" not in md
    assert "## 6.1 激活（待解决）缺陷清单" in md
    assert "## 6.2" not in md


def test_section6_only_postponed_renumbers():
    stats = _stats([{"id": 1, "status": "resolved", "resolution": "postponed", "module": "/A", "severity": 3}])
    md = render_report(stats)
    assert "## 6.1 延期处理缺陷清单" in md
    assert "激活（待解决）缺陷清单" not in md


def test_section6_both_empty_no_section():
    md = render_report(_stats([]))
    # 两类皆空：整个第 6 节连标题一起不显示，正文止于第 5 节
    assert "# 6. 缺陷清单" not in md
    assert "## 6.1" not in md and "## 6.2" not in md
    assert "延期处理缺陷清单" not in md and "激活（待解决）缺陷清单" not in md
    assert "下列清单内嵌本版本需重点关注的两类缺陷" not in md


# ---- C 方案：文档内嵌多维表格块 ----
from report_agent.tools import push_feishu as pf


def test_section6_inline_note_no_link_no_wide_table():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    md = render_report(stats, defect_bitable_inline=True)
    assert "缺陷详细见bug清单" in md
    assert "遗留未解决 1 条" in md
    assert "本次测试共汇总遗留未解决 1 条、延期处理 0 条" in md
    assert "[缺陷清单（多维表格）]" not in md   # 内嵌形态不放链接
    assert "## 6.1" not in md                  # 也不放宽表（章节标题已上调为一级，断言随新口径）


class _EmbedFakeClient(FeishuClient):
    """拦截内嵌链路上的所有 HTTP 动词，记录 (verb, path)，验证编排不联网。"""

    def __init__(self, feishu_cfg) -> None:
        super().__init__(feishu_cfg)
        self.calls = []

    def get_tenant_token(self):
        return "t"

    def _record(self, verb, path, body=None):
        self.calls.append((verb, path, body))
        return {"code": 0, "data": {}}

    def _post(self, path, json_body=None, data=None, files=None, **kw):
        self._record("POST", path, json_body)
        if "/blocks/" in path and path.endswith("/children"):
            return {"code": 0, "data": {"children": [{"block_id": "b1", "bitable": {"token": "appX_tbl1"}}]}}
        if path.endswith("/tables"):
            return {"code": 0, "data": {"table_id": "tbl2"}}
        return {"code": 0, "data": {}}

    def _get(self, path, params=None, **kw):
        self._record("GET", path, None)
        if path.endswith("/fields"):
            return {"code": 0, "data": {"items": [
                {"field_id": "fldP", "is_primary": True},
                {"field_id": "fldA", "is_primary": False},
                {"field_id": "fldB", "is_primary": False}]}}
        if path.endswith("/records"):
            return {"code": 0, "data": {"items": [{"record_id": "r1"}, {"record_id": "r2"}, {"record_id": "r3"}]}}
        return {"code": 0, "data": {}}

    def _put(self, path, json_body=None, **kw):
        self._record("PUT", path, json_body)
        return {"code": 0, "data": {}}

    def _patch(self, path, json_body=None, **kw):
        self._record("PATCH", path, json_body)
        return {"code": 0, "data": {}}

    def _delete(self, path, json_body=None, **kw):
        self._record("DELETE", path, json_body)
        return {"code": 0, "data": {}}


def test_embed_defect_bitable_orchestration():
    cfg = load_config(**{**BASE, "feishu.enabled": True, "feishu.doc_base_url": "https://x.feishu.cn"})
    fake = _EmbedFakeClient(cfg.feishu)
    groups = [("6.1 当前版本遗留未解决bug", [{"序号": "1"}]), ("6.2 当前版本延期处理bug", [{"序号": "1"}, {"序号": "2"}])]
    apps = fake.embed_defect_bitable("docX", groups, DEFECT_FIELD_SPECS)
    assert apps == [("appX", "tbl1"), ("appX", "tbl1")]  # 每组各一个内嵌块
    verbs = [(v, p) for v, p, _ in fake.calls]
    # 两组→每组的 children 插入：一个标题块（block_type=5）+ 一个多维表格块（block_type=18）
    child_calls = [b for v, p, b in fake.calls if v == "POST" and "/blocks/" in p and p.endswith("/children")]
    bitable_children = [b for b in child_calls if b["children"][0]["block_type"] == 18]
    heading_children = [b for b in child_calls if b["children"][0]["block_type"] == 5]
    assert len(bitable_children) == 2 and len(heading_children) == 2
    # 标题块文案与顺序
    assert [b["children"][0]["heading3"]["elements"][0]["text_run"]["content"] for b in heading_children] == \
        ["6.1 当前版本遗留未解决bug", "6.2 当前版本延期处理bug"]
    # 每块：主列改名 PUT ×2；两个默认列 DELETE ×2（共 4）
    assert sum(1 for v, p in verbs if v == "PUT" and p.endswith("/fields/fldP")) == 2
    assert sum(1 for v, p in verbs if v == "DELETE" and "/fields/" in p) == 4
    # 每块补建 12 列（field_specs 去掉首列）×2
    assert sum(1 for v, p in verbs if v == "POST" and p.endswith("/tables/tbl1/fields")) == 2 * (len(DEFECT_FIELD_SPECS) - 1)
    # 每块清理默认空行（batch_delete 含 3 个 id）×2
    del_calls = [b for v, p, b in fake.calls if v == "POST" and p.endswith("/records/batch_delete")]
    assert len(del_calls) == 2 and del_calls[0]["records"] == ["r1", "r2", "r3"]
    # 每块默认表改名 PATCH ×2；两组各写记录 batch_create ×2
    assert sum(1 for v, p in verbs if v == "PATCH" and p.endswith("/tables/tbl1")) == 2
    assert sum(1 for v, p in verbs if p.endswith("/records/batch_create")) == 2
    # 不再在同一 app 内新建第二张表（改为独立块）
    assert ("POST", "/bitable/v1/apps/appX/tables") not in verbs


def test_defect_embed_mode_gating():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    on = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                        "feishu.defect_bitable_embed": True})
    assert rr.RenderReportTool(on)._defect_embed_mode(stats) is True
    off = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                         "feishu.defect_bitable_embed": False})
    assert rr.RenderReportTool(off)._defect_embed_mode(stats) is False
    norows = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                             "feishu.defect_bitable_embed": True})
    assert rr.RenderReportTool(norows)._defect_embed_mode(_stats([])) is False


def test_push_embed_defects_gating_and_success():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    # 无 stats_obj / 开关关 → 空串
    cfg_off = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                             "feishu.defect_bitable_embed": False}).feishu
    assert pf.PushFeishuTool._embed_defects(None, cfg_off, "docX", stats) == ""
    assert pf.PushFeishuTool._embed_defects(None, cfg_off, "docX", None) == ""
    # 开关开 + 桩 client → 返回 app_token
    cfg_on = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                            "feishu.defect_bitable_embed": True}).feishu
    fake = _EmbedFakeClient(cfg_on)
    # 新口径：postponed 为空→不内嵌该组，只剩激活一张表（旧行为为两张含空表）
    assert pf.PushFeishuTool._embed_defects(fake, cfg_on, "docX", stats) == "appX"
    heading_texts = [b["children"][0]["heading3"]["elements"][0]["text_run"]["content"]
                     for v, _p, b in fake.calls
                     if v == "POST" and isinstance(b, dict) and "children" in b and b["children"][0].get("block_type") == 5]
    assert heading_texts == ["6.1 当前版本遗留未解决bug"]


def test_push_embed_defects_swallows_exception():
    stats = _stats([{"id": 1, "status": "active", "resolution": "", "module": "/A", "severity": 3}])
    cfg_on = load_config(**{**BASE, "feishu.enabled": True, "feishu.defect_bitable": True,
                            "feishu.defect_bitable_embed": True}).feishu

    class _Boom(_EmbedFakeClient):
        def embed_defect_bitable(self, document_id, groups, field_specs):
            raise RuntimeError("boom")

    assert pf.PushFeishuTool._embed_defects(_Boom(cfg_on), cfg_on, "docX", stats) == ""
