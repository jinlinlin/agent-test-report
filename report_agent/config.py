"""配置加载：settings.yaml + 环境变量（.env）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv 可选
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


@dataclass
class LLMConfig:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 2048
    use_native_fc: bool = False
    api_key: str = ""


@dataclass
class AgentConfig:
    max_steps: int = 12
    clarify_max_times: int = 1
    interactive_clarify: bool = False  # V2-1：开启后模糊输入走交互澄清回合；默认关时行为与 V1 逐字一致
    llm_intent_fallback: bool = False  # V2-1 延伸：规则未命中时 LLM 在候选集内翻译意图；默认关时不产生任何 LLM 调用


@dataclass
class ZentaoConfig:
    base_url: str = ""
    mode: str = "mock"  # mock | live
    timeout_seconds: int = 10
    token: str = ""            # 预置只读 token（可选）
    account: str = ""          # 账号密码换 token（禅道 REST 标准做法）
    password: str = ""
    api_prefix: str = "/api.php/v1"
    page_size: int = 100
    default_product: str = "UniResearch"   # 输入未能识别到产品时的兜底关键字
    # 字段映射（§10：不同禅道版本字段名可能不同，全部配置化，用 --probe 核对后调整）
    field_product_name: str = "name"
    field_product_desc: str = "desc"
    field_module: str = "module"
    field_severity: str = "severity"
    field_status: str = "status"
    field_resolution: str = "resolution"
    field_activated: str = "activatedCount"
    field_build: str = "openedBuild"   # 影响版本字段（禅道将版本建模为缺陷的 openedBuild）
    field_opened_date: str = "openedDate"  # 缺陷创建时间字段（用于实算测试起止时间）
    field_opened_by: str = "openedBy"      # 缺陷提交人（测试人员/测试负责人来源）
    field_resolved_by: str = "resolvedBy"  # 缺陷解决人（软件工程师来源）
    field_product_owner: str = ""          # 项目负责人在产品对象上的字段名（留空则不取、标待补充；用 --probe 核对后填 PO/PM 等）
    # 缺陷清单（第 6 节）展示字段（仅用于明细表呈现，不影响统计口径；字段名走配置应对禅道版本差异）
    field_title: str = "title"             # bug 标题
    field_priority: str = "pri"            # 优先级（禅道键名为 pri，非 priority）
    field_type: str = "type"               # 功能类型（缺陷类型）
    field_steps: str = "steps"             # 重现步骤（禅道为 HTML，渲染时净化）
    field_assigned_to: str = "assignedTo"  # 指派给
    # 测试起止时间：默认由本版本缺陷创建时间实算；此处仅在无可用日期时作兜底/手工覆盖（§10）
    test_start: str = ""
    test_end: str = ""


@dataclass
class FeishuConfig:
    """飞书云文档/知识库推送配置（V2 技术验证脚手架）。

    凭据（app_id/app_secret）与各 token 一律走 .env，不写进 settings.yaml；
    enabled 默认 False，仅 --push-feishu 或显式开启才推送。
    """
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    api_base: str = "https://open.feishu.cn/open-apis"
    doc_base_url: str = "https://feishu.cn"   # 个人版为 https://my.feishu.cn，按租户改
    target: str = "wiki"                       # wiki=导入后移动到知识库节点；drive=仅导入到云空间
    upload_folder_token: str = ""              # 导入的临时上传落点（Drive folder_token）
    wiki_space_id: str = ""                    # 知识库空间 ID
    wiki_parent_node: str = ""                 # 兜底默认落点 node_token（无匹配子节点时用）
    wiki_projects_parent: str = ""             # 各项目子节点的容器 node_token（按产品名在其下路由同名子节点）
    import_poll_seconds: int = 30
    import_as_docx: bool = True                # 先用本地 pandoc 把 md 转成内嵌图表的 docx 再导入（失败自动回退 md）
    chart_display_scale: float = 1.0           # 仅放大图片在飞书文档中的显示尺寸（等比倍率，1.0=不变；不改页面/正文）
    defect_bitable: bool = True                # 开启飞书时，第 6 节缺陷清单以多维表格呈现（未启用/无缺陷则回退正文宽表）
    defect_bitable_embed: bool = True              # 多维表格以“文档内嵌块”展开在正文（C）；false 则回退为正文内嵌宽表（不再建独立知识库节点）


@dataclass
class MockConfig:
    fault: str = "none"  # none | not_found | timeout | error | empty_bugs


@dataclass
class TraceConfig:
    enabled: bool = True
    dir: str = "traces"


@dataclass
class ReportConfig:
    output_dir: str = "output"
    polish_conclusion: bool = False  # F17：开启后 5.1 发布建议由 LLM 润色（带数字一致性校验，校验失败回退模板原文）


@dataclass
class ChartsConfig:
    """matplotlib 本地图表（Phase 1）：失败逐图降级为占位文字，不阻断报告。"""
    enabled: bool = True
    dpi: int = 150


@dataclass
class MemoryConfig:
    """项目级长期记忆（V2-3）：跨版本复用「同一项目恒定」的参考文档链接（手册/UI/原型；需求文档走检索）。

    doc_links_enabled 默认 False：关闭时不读写小抄，§1.3 行为与现状逐字一致。
    """
    doc_links_enabled: bool = False
    dir: str = "memory"
    doc_links_file: str = "doc_links.json"


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    zentao: ZentaoConfig = field(default_factory=ZentaoConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    mock: MockConfig = field(default_factory=MockConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    charts: ChartsConfig = field(default_factory=ChartsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @property
    def trace_dir(self) -> Path:
        return REPO_ROOT / self.trace.dir

    @property
    def doc_links_path(self) -> Path:
        return REPO_ROOT / self.memory.dir / self.memory.doc_links_file

    @property
    def report_dir(self) -> Path:
        return REPO_ROOT / self.report.output_dir


def _get(d: Dict[str, Any], key: str, default: Any) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def load_config(settings_path: Path | str = SETTINGS_PATH, **overrides) -> Config:
    """加载配置。overrides 支持点路径覆盖，如 {'mock.fault': 'timeout', 'agent.max_steps': 3}。"""
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / "config" / ".env")

    raw: Dict[str, Any] = {}
    p = Path(settings_path)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    cfg = Config(
        llm=LLMConfig(
            base_url=_get(_get(raw, "llm", {}), "base_url", LLMConfig.base_url),
            model=_get(_get(raw, "llm", {}), "model", LLMConfig.model),
            temperature=_get(_get(raw, "llm", {}), "temperature", LLMConfig.temperature),
            max_tokens=_get(_get(raw, "llm", {}), "max_tokens", LLMConfig.max_tokens),
            use_native_fc=_get(_get(raw, "llm", {}), "use_native_fc", False),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        ),
        agent=AgentConfig(
            max_steps=_get(_get(raw, "agent", {}), "max_steps", 12),
            clarify_max_times=_get(_get(raw, "agent", {}), "clarify_max_times", 1),
            interactive_clarify=str(_get(_get(raw, "agent", {}), "interactive_clarify", False)).lower() in ("1", "true", "yes", "on"),
            llm_intent_fallback=str(_get(_get(raw, "agent", {}), "llm_intent_fallback", False)).lower() in ("1", "true", "yes", "on"),
        ),
        zentao=ZentaoConfig(
            base_url=os.getenv("ZENTAO_BASE_URL", "") or _get(_get(raw, "zentao", {}), "base_url", ""),
            mode=_get(_get(raw, "zentao", {}), "mode", "mock"),
            timeout_seconds=_get(_get(raw, "zentao", {}), "timeout_seconds", 10),
            token=os.getenv("ZENTAO_TOKEN", "") or _get(_get(raw, "zentao", {}), "token", ""),
            account=os.getenv("ZENTAO_ACCOUNT", "") or _get(_get(raw, "zentao", {}), "account", ""),
            password=os.getenv("ZENTAO_PASSWORD", "") or _get(_get(raw, "zentao", {}), "password", ""),
            api_prefix=_get(_get(raw, "zentao", {}), "api_prefix", "/api.php/v1"),
            page_size=_get(_get(raw, "zentao", {}), "page_size", 100),
            default_product=os.getenv("ZENTAO_DEFAULT_PRODUCT", "") or _get(_get(raw, "zentao", {}), "default_product", "UniResearch"),
            field_product_name=_get(_get(raw, "zentao", {}), "field_product_name", "name"),
            field_product_desc=_get(_get(raw, "zentao", {}), "field_product_desc", "desc"),
            field_module=_get(_get(raw, "zentao", {}), "field_module", "module"),
            field_severity=_get(_get(raw, "zentao", {}), "field_severity", "severity"),
            field_status=_get(_get(raw, "zentao", {}), "field_status", "status"),
            field_resolution=_get(_get(raw, "zentao", {}), "field_resolution", "resolution"),
            field_activated=_get(_get(raw, "zentao", {}), "field_activated", "activatedCount"),
            field_build=_get(_get(raw, "zentao", {}), "field_build", "openedBuild"),
            field_opened_date=_get(_get(raw, "zentao", {}), "field_opened_date", "openedDate"),
            field_opened_by=_get(_get(raw, "zentao", {}), "field_opened_by", "openedBy"),
            field_resolved_by=_get(_get(raw, "zentao", {}), "field_resolved_by", "resolvedBy"),
            field_product_owner=os.getenv("ZENTAO_FIELD_PRODUCT_OWNER", "") or _get(_get(raw, "zentao", {}), "field_product_owner", ""),
            field_title=_get(_get(raw, "zentao", {}), "field_title", "title"),
            field_priority=_get(_get(raw, "zentao", {}), "field_priority", "pri"),
            field_type=_get(_get(raw, "zentao", {}), "field_type", "type"),
            field_steps=_get(_get(raw, "zentao", {}), "field_steps", "steps"),
            field_assigned_to=_get(_get(raw, "zentao", {}), "field_assigned_to", "assignedTo"),
            test_start=_get(_get(raw, "zentao", {}), "test_start", ""),
            test_end=_get(_get(raw, "zentao", {}), "test_end", ""),
        ),
        feishu=FeishuConfig(
            enabled=str(_get(_get(raw, "feishu", {}), "enabled", "false")).lower() in ("1", "true", "yes", "on"),
            app_id=os.getenv("FEISHU_APP_ID", "") or _get(_get(raw, "feishu", {}), "app_id", ""),
            app_secret=os.getenv("FEISHU_APP_SECRET", "") or _get(_get(raw, "feishu", {}), "app_secret", ""),
            api_base=_get(_get(raw, "feishu", {}), "api_base", "https://open.feishu.cn/open-apis"),
            doc_base_url=os.getenv("FEISHU_DOC_BASE_URL", "") or _get(_get(raw, "feishu", {}), "doc_base_url", "https://feishu.cn"),
            target=_get(_get(raw, "feishu", {}), "target", "wiki"),
            upload_folder_token=os.getenv("FEISHU_FOLDER_TOKEN", "") or _get(_get(raw, "feishu", {}), "upload_folder_token", ""),
            wiki_space_id=os.getenv("FEISHU_WIKI_SPACE_ID", "") or _get(_get(raw, "feishu", {}), "wiki_space_id", ""),
            wiki_parent_node=os.getenv("FEISHU_WIKI_PARENT_NODE", "") or _get(_get(raw, "feishu", {}), "wiki_parent_node", ""),
            wiki_projects_parent=os.getenv("FEISHU_WIKI_PROJECTS_PARENT", "") or _get(_get(raw, "feishu", {}), "wiki_projects_parent", ""),
            import_poll_seconds=_get(_get(raw, "feishu", {}), "import_poll_seconds", 30),
            import_as_docx=str(_get(_get(raw, "feishu", {}), "import_as_docx", True)).lower() in ("1", "true", "yes", "on"),
            chart_display_scale=float(_get(_get(raw, "feishu", {}), "chart_display_scale", 1.0)),
            defect_bitable=str(_get(_get(raw, "feishu", {}), "defect_bitable", True)).lower() in ("1", "true", "yes", "on"),
            defect_bitable_embed=str(_get(_get(raw, "feishu", {}), "defect_bitable_embed", True)).lower() in ("1", "true", "yes", "on"),
        ),
        mock=MockConfig(fault=_get(_get(raw, "mock", {}), "fault", "none")),
        trace=TraceConfig(
            enabled=_get(_get(raw, "trace", {}), "enabled", True),
            dir=_get(_get(raw, "trace", {}), "dir", "traces"),
        ),
        report=ReportConfig(
            output_dir=_get(_get(raw, "report", {}), "output_dir", "output"),
            polish_conclusion=str(_get(_get(raw, "report", {}), "polish_conclusion", False)).lower() in ("1", "true", "yes", "on"),
        ),
        charts=ChartsConfig(
            enabled=_get(_get(raw, "charts", {}), "enabled", True),
            dpi=_get(_get(raw, "charts", {}), "dpi", 150),
        ),
        memory=MemoryConfig(
            doc_links_enabled=str(_get(_get(raw, "memory", {}), "doc_links_enabled", False)).lower() in ("1", "true", "yes", "on"),
            dir=_get(_get(raw, "memory", {}), "dir", "memory"),
            doc_links_file=_get(_get(raw, "memory", {}), "doc_links_file", "doc_links.json"),
        ),
    )

    # 应用 overrides（点路径）
    for path, value in overrides.items():
        parts = path.split(".")
        obj: Any = cfg
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)

    return cfg
