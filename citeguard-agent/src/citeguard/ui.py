import streamlit as st

from citeguard.workflow import build_fixture_agent

st.set_page_config(page_title="CiteGuard", page_icon="C")
st.title("CiteGuard")
st.caption("Evidence-grounded document research Agent")
question = st.text_input("Question", "What does the document say about evaluation?")
if st.button("Ask"):
    result = build_fixture_agent().run(question)
    st.subheader("Answer")
    st.write(result.answer)
    st.metric("Support rate", f"{result.verification.support_rate:.0%}")
    st.subheader("Evidence")
    for chunk in result.evidence:
        st.info(f"[{chunk.id}] {chunk.text}")
    with st.expander("Trace"):
        st.json(result.trace.model_dump())
