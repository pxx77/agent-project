from __future__ import annotations

import json
import httpx


class MockClient:
    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        if "drop" in user.lower() or "delete" in user.lower() or "update" in user.lower():
            return {"sql": user}
        return {"sql": 'SELECT region, SUM(CAST(revenue AS REAL)) AS total_revenue FROM sales GROUP BY region ORDER BY total_revenue DESC'}


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        response = httpx.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "response_format": {"type": "json_object"}}, timeout=45)
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])
