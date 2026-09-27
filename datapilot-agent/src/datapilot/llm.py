from __future__ import annotations

import difflib
import json
import re
import httpx

from .config import Settings

_MISSING_COLUMN = re.compile(r"no such column:\s*([^\s,)]+)", re.I)


class MockClient:
    provider = "mock"
    model = "deterministic-fixture"

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        context = json.loads(system)
        if schema_name == "sql_repair":
            return self._repair(context)
        if "drop" in user.lower() or "delete" in user.lower() or "update" in user.lower():
            return {"sql": user}
        table = context["table"]
        columns = context["columns"]
        text_columns = [column["name"] for column in columns if column["type"] == "string"]
        number_columns = [column["name"] for column in columns if column["type"] == "number"]
        group = text_columns[0] if text_columns else columns[0]["name"]
        value = number_columns[0] if number_columns else columns[-1]["name"]
        return {
            "sql": (
                f'SELECT "{group}", SUM(CAST("{value}" AS REAL)) AS total_{value} '
                f'FROM "{table}" GROUP BY "{group}" ORDER BY total_{value} DESC'
            )
        }

    def _repair(self, context: dict) -> dict:
        """Rewrite unknown column names to the closest column in the schema."""
        failed_sql = str(context.get("failed_sql", ""))
        error = str(context.get("error", ""))
        known = [column["name"] for column in context.get("columns", []) if column.get("name")]
        match = _MISSING_COLUMN.search(error)
        if not match or not known:
            return {"sql": failed_sql}
        missing = match.group(1).strip('"').split(".")[-1]
        closest = difflib.get_close_matches(missing, known, n=1)
        if not closest:
            return {"sql": failed_sql}
        return {"sql": re.sub(rf'"?{re.escape(missing)}"?', f'"{closest[0]}"', failed_sql, count=1)}


class DeepSeekClient:
    provider = "deepseek"

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        response = httpx.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "response_format": {"type": "json_object"}}, timeout=45)
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])


def build_model_client(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    if not settings.model_enabled:
        return MockClient()
    return DeepSeekClient(
        api_key=settings.api_key or "",
        base_url=settings.base_url,
        model=settings.model,
    )
