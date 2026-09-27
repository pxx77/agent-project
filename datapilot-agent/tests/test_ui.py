from pathlib import Path


def test_streamlit_ui_has_file_uploader():
    source = (Path(__file__).parents[1] / "src" / "datapilot" / "ui.py").read_text(encoding="utf-8")
    assert "st.file_uploader" in source
    assert 'type=["csv", "xlsx", "sqlite"]' in source
