"""Trace 记录器：逐轮记录 step/type/输入输出/State 快照/耗时/失败原因，并落盘 JSON。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class Tracer:
    def __init__(self, enabled: bool = True, trace_dir: Optional[Path] = None) -> None:
        self.enabled = enabled
        self.trace_dir = trace_dir
        self.records: List[Dict[str, Any]] = []
        self._t0 = time.time()

    def _elapsed_ms(self) -> int:
        return int((time.time() - self._t0) * 1000)

    def log(self, *, step: int, type: str, thought: str = "", tool_name: str = "",
            tool_args: Any = None, tool_result: Any = None, state_snapshot: Any = None,
            fail_reason: str = "", user_input: str = "") -> None:
        if not self.enabled:
            return
        self.records.append({
            "step": step,
            "type": type,  # llm_reasoning | tool_call | terminate
            "user_input": user_input,
            "llm_thought": thought,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result,
            "state_snapshot": state_snapshot,
            "elapsed_ms": self._elapsed_ms(),
            "fail_reason": fail_reason,
        })

    def save(self, filename: str) -> Optional[Path]:
        if not self.enabled or self.trace_dir is None:
            return None
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / filename
        path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
