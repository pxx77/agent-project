import pandas as pd
import streamlit as st

from datapilot.config import Settings
from datapilot.ingestion import load_bytes, load_fixture
from datapilot.profiling import profile_dataset
from datapilot.workflow import run_agent

st.set_page_config(page_title="DataPilot", page_icon="D", layout="wide")
st.title("DataPilot")
st.caption("上传数据，用自然语言生成安全只读 SQL，并核对查询结果。")

settings = Settings.from_env()
provider = "DeepSeek API" if settings.model_enabled else "Mock 离线演示"
st.info(f"当前 SQL 生成来源：{provider} | 模型：{settings.model if settings.model_enabled else 'deterministic-fixture'}")

if "datapilot_handle" not in st.session_state:
    st.session_state.datapilot_handle = load_fixture()
    st.session_state.datapilot_source = "内置 sales.csv"

st.subheader("1. 上传数据")
uploaded_file = st.file_uploader(
    "选择 CSV、XLSX 或 SQLite",
    type=["csv", "xlsx", "sqlite"],
    help="上传后点击加载数据；后续提问只针对当前数据集。",
)
if st.button("加载并分析字段", disabled=uploaded_file is None, type="primary"):
    try:
        handle = load_bytes(uploaded_file.name, uploaded_file.getvalue(), settings.max_rows)
        st.session_state.datapilot_handle = handle
        st.session_state.datapilot_source = uploaded_file.name
        st.success(f"数据加载成功：{uploaded_file.name}，{len(handle.rows)} 行")
    except Exception as exc:
        st.error(f"数据加载失败：{exc}")

handle = st.session_state.datapilot_handle
profile = profile_dataset(handle)
st.caption(f"当前数据源：{st.session_state.datapilot_source}")
with st.expander("查看字段画像"):
    st.json(profile.model_dump())

st.subheader("2. 提出分析问题")
question = st.text_area(
    "问题",
    value="按类别汇总数值，并按汇总结果降序排列。",
    height=90,
)
if st.button("运行 DataPilot Agent", disabled=not question.strip(), type="primary"):
    try:
        with st.spinner("正在生成、检查并执行只读 SQL..."):
            result = run_agent(question, handle)
        usage = result.trace.usage
        cost = result.trace.cost_cny
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("执行状态", "成功" if result.query_result.error is None else "失败")
        col2.metric("返回行数", result.query_result.row_count)
        col3.metric("SQL 来源", result.trace.provider)
        col4.metric("本次费用", f"¥{cost:.4f}" if cost is not None else "未计价")
        st.caption(
            f"本次用量 {usage.total_tokens} tokens（输入 {usage.prompt_tokens} / 输出 {usage.completion_tokens}）"
            f"｜{'实测' if usage.measured else '估算'}"
            f"｜计价模型 {result.trace.billing_model or '未计价'}"
        )
        st.subheader("生成的 SQL")
        st.code(result.sql, language="sql")
        if result.query_result.error:
            st.error(result.query_result.error)
        else:
            frame = pd.DataFrame(result.query_result.rows, columns=result.query_result.columns)
            st.subheader("查询结果")
            st.dataframe(frame, use_container_width=True)
            st.subheader("结论")
            st.write(result.conclusion)
            st.json(result.verification.model_dump())
        with st.expander("运行轨迹"):
            st.json(result.trace.model_dump())
    except Exception as exc:
        st.error(f"Agent 运行失败：{exc}")
