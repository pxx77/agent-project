from pathlib import Path


def test_streamlit_ui_has_file_uploader():
    source = (Path(__file__).parents[1] / "src" / "datapilot" / "ui.py").read_text(encoding="utf-8")
    assert "st.file_uploader" in source
    assert 'type=["csv", "xlsx", "sqlite"]' in source


def test_streamlit_ui_reports_token_usage_and_cost():
    """费用与 token 用量要出现在界面上，否则评测报告里的数字在演示时看不到。"""
    source = (Path(__file__).parents[1] / "src" / "datapilot" / "ui.py").read_text(encoding="utf-8")
    assert "result.trace.usage" in source
    assert "result.trace.cost_cny" in source
    assert "result.trace.billing_model" in source
    assert '"本次费用"' in source
    assert "本次用量" in source
    # 实测与估算必须能区分，否则离线估算会被当成真实花费。
    assert "'实测'" in source and "'估算'" in source
    # 价格表里没有的模型要显示「未计价」，而不是显示 0 元。
    assert "未计价" in source
