"""OpenAI 兼容接口客户端（懒加载 openai SDK，无 Key 时标记不可用）。

支持两种调用模式：
- complete(): 纯文本 prompt → 文本响应（prompt-based planner 使用）
- complete_with_tools(): 原生 Function Calling 协议，传入 tools 定义，返回结构化的 tool_calls
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class LLMClient:
    def __init__(self, cfg) -> None:
        self.cfg = cfg.llm
        self._client = None
        self.available = bool(self.cfg.api_key)

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI  # 懒加载，未安装/未用时不影响离线链路

            self._client = OpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        return self._client

    # ---- 纯文本调用（现有逻辑保持不变）----
    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if not self.available:
            raise RuntimeError("LLM 不可用：缺少 OPENAI_API_KEY")
        client = self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ---- 原生 Function Calling 调用 ----
    def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ):
        """发送带 tools 定义的请求，返回原始 response message 对象。

        调用方通过 message.tool_calls 获取模型选择的工具及参数，
        或通过 message.content 获取模型的最终文本回答。
        """
        if not self.available:
            raise RuntimeError("LLM 不可用：缺少 OPENAI_API_KEY")
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
        )
        return resp.choices[0].message
