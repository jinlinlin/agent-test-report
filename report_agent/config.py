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
    use_llm_planner: bool = True
    api_key: str = ""


@dataclass
class AgentConfig:
    max_steps: int = 12
    clarify_max_times: int = 1


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
    # 字段映射（§10：不同禅道版本字段名可能不同，全部配置化，用 --probe 核对后调整）
    field_product_name: str = "name"
    field_product_desc: str = "desc"
    field_module: str = "module"
    field_severity: str = "severity"
    field_status: str = "status"
    field_resolution: str = "resolution"
    field_activated: str = "activatedCount"
    field_build: str = "openedBuild"   # 影响版本字段（禅道将版本建模为缺陷的 openedBuild）
    # 测试起止时间占位（禅道 REST 无干净产品版本日期字段时手填，§10）
    test_start: str = ""
    test_end: str = ""


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


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    zentao: ZentaoConfig = field(default_factory=ZentaoConfig)
    mock: MockConfig = field(default_factory=MockConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    @property
    def trace_dir(self) -> Path:
        return REPO_ROOT / self.trace.dir

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
            use_llm_planner=_get(_get(raw, "llm", {}), "use_llm_planner", True),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        ),
        agent=AgentConfig(
            max_steps=_get(_get(raw, "agent", {}), "max_steps", 12),
            clarify_max_times=_get(_get(raw, "agent", {}), "clarify_max_times", 1),
        ),
        zentao=ZentaoConfig(
            base_url=_get(_get(raw, "zentao", {}), "base_url", ""),
            mode=_get(_get(raw, "zentao", {}), "mode", "mock"),
            timeout_seconds=_get(_get(raw, "zentao", {}), "timeout_seconds", 10),
            token=os.getenv("ZENTAO_TOKEN", "") or _get(_get(raw, "zentao", {}), "token", ""),
            account=os.getenv("ZENTAO_ACCOUNT", "") or _get(_get(raw, "zentao", {}), "account", ""),
            password=os.getenv("ZENTAO_PASSWORD", "") or _get(_get(raw, "zentao", {}), "password", ""),
            api_prefix=_get(_get(raw, "zentao", {}), "api_prefix", "/api.php/v1"),
            page_size=_get(_get(raw, "zentao", {}), "page_size", 100),
            field_product_name=_get(_get(raw, "zentao", {}), "field_product_name", "name"),
            field_product_desc=_get(_get(raw, "zentao", {}), "field_product_desc", "desc"),
            field_module=_get(_get(raw, "zentao", {}), "field_module", "module"),
            field_severity=_get(_get(raw, "zentao", {}), "field_severity", "severity"),
            field_status=_get(_get(raw, "zentao", {}), "field_status", "status"),
            field_resolution=_get(_get(raw, "zentao", {}), "field_resolution", "resolution"),
            field_activated=_get(_get(raw, "zentao", {}), "field_activated", "activatedCount"),
            field_build=_get(_get(raw, "zentao", {}), "field_build", "openedBuild"),
            test_start=_get(_get(raw, "zentao", {}), "test_start", ""),
            test_end=_get(_get(raw, "zentao", {}), "test_end", ""),
        ),
        mock=MockConfig(fault=_get(_get(raw, "mock", {}), "fault", "none")),
        trace=TraceConfig(
            enabled=_get(_get(raw, "trace", {}), "enabled", True),
            dir=_get(_get(raw, "trace", {}), "dir", "traces"),
        ),
        report=ReportConfig(output_dir=_get(_get(raw, "report", {}), "output_dir", "output")),
    )

    # 应用 overrides（点路径）
    for path, value in overrides.items():
        parts = path.split(".")
        obj: Any = cfg
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)

    return cfg
