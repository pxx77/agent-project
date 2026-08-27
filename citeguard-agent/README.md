# CiteGuard

CiteGuard checks claims against source documents and returns evidence-backed citations.

## Setup

```bash
uv sync --extra dev
cp .env.example .env
```

Set `CITEGUARD_MOCK=1` for local runs without a DeepSeek API key. For model-backed runs,
set `DEEPSEEK_API_KEY` in `.env`; never commit that value.

Run the configuration test with:

```bash
uv run pytest tests/test_config.py -v
```
