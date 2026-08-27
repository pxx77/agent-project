from __future__ import annotations

import json
from typing import Protocol

import httpx


class ModelClient(Protocol):
    def complete_json(self, system: str, user: str, schema_name: str) -> dict: ...


class MockClient:
    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        return {"answer": "检索到的证据支持该结论。", "claims": [{"text": "该结论来自检索证据。", "citations": []}]}


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        response = httpx.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "response_format": {"type": "json_object"}}, timeout=45)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
