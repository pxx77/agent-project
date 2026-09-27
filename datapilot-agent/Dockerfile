# The image runs the read-only analysis API in offline mode so it needs no API key. Pass
# `-e DATAPILOT_MOCK=0 -e DEEPSEEK_API_KEY=...` to reach DeepSeek instead.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    DATAPILOT_MOCK=1 \
    DATAPILOT_FIXTURES=/app/fixtures

# pyproject.toml does not declare a `readme`, but README.md is copied alongside it because the
# build backend reads the file list from the project directory.
COPY pyproject.toml README.md ./
COPY src ./src
# `fixtures/` is not a Python package, so the wheel never contains it. `src/datapilot/state.py`
# loads `fixtures/sales.csv` while it is imported, and `src/datapilot/ingestion.py` reads
# `DATAPILOT_FIXTURES` to find the copy that lands here.
COPY fixtures ./fixtures

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser

# 8766 is the port the project README and `tools/deepseek_launcher.ps1` both use for this API.
EXPOSE 8766

USER appuser

CMD ["uvicorn", "datapilot.api:app", "--host", "0.0.0.0", "--port", "8766"]
