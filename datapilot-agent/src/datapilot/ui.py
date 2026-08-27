import streamlit as st

from datapilot.workflow import build_fixture_agent

st.set_page_config(page_title="DataPilot", page_icon="D")
st.title("DataPilot")
st.caption("Safe natural-language data analysis Agent")
question = st.text_input("Question", "What is revenue by region?")
if st.button("Analyze"):
    result = build_fixture_agent().run(question)
    st.code(result.sql, language="sql")
    if result.query_result.error:
        st.error(result.query_result.error)
    else:
        st.dataframe(result.query_result.rows, use_container_width=True)
        st.write(result.conclusion)
        st.json(result.verification.model_dump())
        with st.expander("Trace"):
            st.json(result.trace.model_dump())
