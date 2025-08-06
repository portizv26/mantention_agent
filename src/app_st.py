# app.py
# ──────────────── Streamlit front-end for AgentChat ────────────────
import io
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from openai import OpenAI

# ✨ BACKEND ─ import the class you refactored earlier
from agent import AgentChat   # adjust the path / name if different


# ───────────────────────────── Helpers ──────────────────────────────
def init_agent(conn) -> AgentChat:
    """Create one AgentChat per browser session."""
    return AgentChat(conn = conn)


# ───────────────────────── sidebar renderer (new) ─────────────────────────
def render_sidebar(artifacts):
    """Show the latest artefacts in the sidebar with smaller fonts."""

    # ── 1 ▸ inject CSS only once per session ─────────────────────────
    if "sidebar_css" not in st.session_state:
        st.sidebar.markdown(
            """
            <style>
            /* section headings */
            .sb-title {font-size: 1rem; font-weight: 600; margin: 0.25em 0 0.5em 0;}

            /* tables */
            .sb-table  {font-size: 0.7rem; border-collapse: collapse; width: 100%;}
            .sb-table th, .sb-table td {padding: 2px 6px;}
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.session_state.sidebar_css = True

    st.sidebar.header("📂 Artifacts", divider="grey")
    st.sidebar.caption("Only the most recent files are kept in memory.")

    # ── 2 ▸ IMAGE ────────────────────────────────────────────────────
    if artifacts.image_file:
        st.sidebar.markdown('<div class="sb-title">🖼️ Image</div>', unsafe_allow_html=True)
        st.sidebar.image(str(artifacts.image_file))
        with open(artifacts.image_file, "rb") as fh:
            st.sidebar.download_button(
                "Download image", fh.read(),
                file_name=artifacts.image_file.name, mime="image/png",
                key=f"dl_image_{artifacts.image_file.name}",
            )

    # ── 3 ▸ DATA ─────────────────────────────────────────────────────
    if artifacts.data_file:
        st.sidebar.markdown('<div class="sb-title">📊 Data (top 5 rows)</div>', unsafe_allow_html=True)
        df = pd.read_csv(artifacts.data_file)
        # use HTML to control font-size
        html_table = df.head(5).to_html(index=False, classes="sb-table")
        st.sidebar.markdown(html_table, unsafe_allow_html=True)
        st.sidebar.download_button(
            "Download CSV", df.to_csv(index=False).encode(),
            file_name=artifacts.data_file.name, mime="text/csv",
            key=f"dl_csv_{artifacts.data_file.name}",
        )

    # ── 4 ▸ CODE ─────────────────────────────────────────────────────
    if artifacts.code_file:
        st.sidebar.markdown('<div class="sb-title">💻 Python code</div>', unsafe_allow_html=True)
        code_txt = Path(artifacts.code_file).read_text()
        # smaller font for code via st.code's own styling; keep default
        st.sidebar.code(code_txt, language="python")
        st.sidebar.download_button(
            "Download .py", code_txt.encode(),
            file_name=artifacts.code_file.name, mime="text/x-python",
            key=f"dl_code_{artifacts.code_file.name}",
        )

# ──────────────────────────── Page setup ───────────────────────────
st.set_page_config(page_title="AI SQL Chat", page_icon="🤖", layout="wide")
st.title("🤖 SQL + Image Chatbot")

# Session state initialisation
if 'conn' not in st.session_state:
    conn = sqlite3.connect("Data/maintenance.db", check_same_thread=False)
    st.session_state.conn = conn
    
# Session state initialisation
if "agent" not in st.session_state:
    st.session_state.agent = init_agent(conn = st.session_state.conn)

if "messages" not in st.session_state:           # list[dict]: {"role", "content"}
    st.session_state.messages = []

agent: AgentChat = st.session_state.agent

# ─── Show previous turns ───────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ─── Chat input ────────────────────────────────────────────────────
prompt = st.chat_input("Escribe tu pregunta…")

if prompt:
    # user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # backend call
    with st.spinner("Pensando…"):
        answer_es = agent.execute(prompt)

    # assistant message
    st.session_state.messages.append({"role": "assistant", "content": answer_es})
    with st.chat_message("assistant"):
        st.markdown(answer_es)

# ─── Render sidebar once per run (avoids duplicate keys) ───────────
render_sidebar(agent.artefacts)