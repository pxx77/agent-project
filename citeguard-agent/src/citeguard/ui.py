import streamlit as st

from citeguard.config import Settings
from citeguard.ingestion import chunk_document, parse_bytes
from citeguard.retrieval import build_index
from citeguard.workflow import build_fixture_agent, run_agent

st.set_page_config(page_title="CiteGuard", page_icon="C", layout="wide")
st.title("CiteGuard")
st.caption("上传文档，提出问题，检查回答引用的原文证据。")

settings = Settings.from_env()
provider = "DeepSeek API" if settings.model_enabled else "Mock 离线演示"
st.info(f"当前回答来源：{provider} | 模型：{settings.model if settings.model_enabled else 'deterministic-fixture'}")

if "citeguard_index" not in st.session_state:
    st.session_state.citeguard_index = build_fixture_agent().index
    st.session_state.citeguard_source = "内置示例文档"

st.subheader("1. 上传文档")
uploaded_files = st.file_uploader(
    "选择 TXT、Markdown、PDF 或 DOCX",
    type=["txt", "md", "pdf", "docx"],
    accept_multiple_files=True,
    help="可同时上传多个文件；点击建立索引后，提问只针对这些文件。",
)
if st.button("建立文档索引", disabled=not uploaded_files, type="primary"):
    try:
        chunks = []
        names = []
        for uploaded_file in uploaded_files:
            data = uploaded_file.getvalue()
            if len(data) > settings.max_file_mb * 1024 * 1024:
                raise ValueError(f"{uploaded_file.name} 超过 {settings.max_file_mb} MB 限制")
            document = parse_bytes(uploaded_file.name, data)
            chunks.extend(chunk_document(document))
            names.append(uploaded_file.name)
        if not chunks:
            raise ValueError("没有从文件中提取到文本")
        st.session_state.citeguard_index = build_index(chunks, settings)
        st.session_state.citeguard_source = "、".join(names)
        st.success(f"索引完成：{len(names)} 个文件，{len(chunks)} 个文本块")
    except Exception as exc:
        st.error(f"文档处理失败：{exc}")

st.caption(f"当前数据源：{st.session_state.citeguard_source}")
st.subheader("2. 提出问题")
question = st.text_area(
    "问题",
    value="文档对 Agent 评测提出了哪些要求？",
    height=90,
)
if st.button("运行 CiteGuard Agent", disabled=not question.strip(), type="primary"):
    try:
        with st.spinner("正在检索、回答并验证引用..."):
            result = run_agent(question, st.session_state.citeguard_index)
        st.subheader("回答")
        st.write(result.answer)
        usage = result.trace.usage
        cost = result.trace.cost_cny
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("证据支持率", f"{result.verification.support_rate:.0%}")
        col2.metric("引用数量", len(result.citations))
        col3.metric("回答来源", result.trace.provider)
        col4.metric("本次费用", f"¥{cost:.4f}" if cost is not None else "未计价")
        st.caption(
            f"本次用量 {usage.total_tokens} tokens（输入 {usage.prompt_tokens} / 输出 {usage.completion_tokens}）"
            f"｜{'实测' if usage.measured else '估算'}"
            f"｜计价模型 {result.trace.billing_model or '未计价'}"
        )
        st.subheader("引用证据")
        for chunk in result.evidence:
            st.info(f"[{chunk.id}] {chunk.text}")
        with st.expander("运行轨迹"):
            st.json(result.trace.model_dump())
    except Exception as exc:
        st.error(f"Agent 运行失败：{exc}")
