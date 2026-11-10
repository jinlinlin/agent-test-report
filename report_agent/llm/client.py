"""OpenAI 兼容接口客户端（懒加载 openai SDK，无 Key 时标记不可用）。"""
from __future__ import annotations

from typing import Optional


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
