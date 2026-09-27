from pathlib import Path


def test_streamlit_ui_has_file_uploader():
    source = (Path(__file__).parents[1] / "src" / "citeguard" / "ui.py").read_text(encoding="utf-8")
    assert "st.file_uploader" in source
    assert "accept_multiple_files=True" in source
