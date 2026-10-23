import json
import uuid

import nav
import requests
import streamlit as st

PAGE = "pages/8_Chat_Assistant.py"

st.set_page_config(page_title="AI Loan Assistant — Loan Portal", layout="wide")
nav.require_login()
nav.render_sidebar(current_page=PAGE)


st.title("🤖 AI Loan Assistant")
st.caption("Ask about loan policies, your application's status, or an EMI estimate. Policy answers cite the clauses they use.")
st.caption("Policy answers come from public HDFC Bank and RBI documents. This is a student demo, not an official HDFC Bank service.")

# ── Application selector ────────────────────────────────────────────────────
resp_apps = nav.api("GET", "/applications", params={"limit": 50, "offset": 0})
all_apps = nav.json_or_none(resp_apps) or []
app_options = {a["id"]: f"{a.get('title') or 'Untitled'} —{a['status'].replace('_', ' ').title()}" for a in all_apps}

preselected = st.session_state.pop("chat_app_id", None)
default_idx = 0
app_ids = [None] + list(app_options.keys())
app_labels = ["No specific application"] + list(app_options.values())
if preselected and preselected in app_options:
    default_idx = app_ids.index(preselected)

selected_app_id = st.selectbox(
    "Chatting about",
    options=app_ids,
    format_func=lambda x: app_labels[app_ids.index(x)],
    index=default_idx,
    label_visibility="visible",
)

# Reset conversation when application selection changes
if st.session_state.get("_last_chat_app") != selected_app_id:
    st.session_state["messages"] = []
    st.session_state["session_id"] = str(uuid.uuid4())
    st.session_state["_last_chat_app"] = selected_app_id

# The backend loads the selected application itself (owner-checked); we only send its id.
if selected_app_id:
    app_info = next((a for a in all_apps if a["id"] == selected_app_id), None)
    if app_info:
        amount = app_info.get("loan_amount_requested")
        amount_str = f" · ₹{amount:,.0f}" if amount else ""
        st.info(
            f"**{app_info.get('title') or 'Untitled'}**{amount_str}"
            f" · Status: {app_info['status'].replace('_', ' ').title()}"
        )

if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if st.button("New chat"):
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())
    st.rerun()


def _show_sources(sources: list[dict]) -> None:
    with st.expander("View Sources"):
        for source in sources:
            pages = source["page"] if source.get("page_end", source["page"]) == source["page"] else f"{source['page']}–{source['page_end']}"
            publisher = f"{source['publisher']} · " if source.get("publisher") else ""
            as_of = f" · as of {source['effective_date']}" if source.get("effective_date") else ""
            st.caption(f"[{source.get('label', '')}] {publisher}{source['document']} — page {pages} · {source['section']}{as_of}")
            st.write(source["excerpt"])


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if message["role"] == "assistant" and message.get("sources"):
            _show_sources(message["sources"])

prompt = None
with st.bottom:
    st.markdown(
        """
        <style>
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-height: 47px !important; height: 47px !important; display: flex !important; align-items: center !important;
        }
        div[data-testid="stSelectbox"] { margin-bottom: 0px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    prompt = st.chat_input("Ask about loan eligibility...")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        status_box = st.status("Analyzing question...", expanded=True)
        answer_box = st.empty()
        answer = ""
        sources: list[dict] = []
        payload = {"question": prompt, "session_id": st.session_state.session_id, "application_id": selected_app_id}
        response = nav.api("POST", "/ask/stream", json=payload, stream=True, timeout=(5, 300))
        if response is None:
            status_box.update(label="Backend unavailable", state="error", expanded=False)
            st.stop()
        with response:
            if response.status_code == 429:
                status_box.update(label="Slow down", state="error", expanded=False)
                st.error("You are sending questions too quickly. Please wait a moment.")
                st.stop()
            if response.status_code != 200:
                status_box.update(label="Error", state="error", expanded=False)
                st.error(nav.detail(response, "The assistant could not process that request."))
                st.stop()
            event = ""
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        data = json.loads(line.removeprefix("data: "))
                        if event == "status" and status_box:
                            status_box.write(data["message"])
                            status_box.update(label=data["message"])
                        elif event == "token":
                            if status_box:
                                status_box.update(label="Done processing", state="complete", expanded=False)
                                status_box = None
                            answer += data["text"]
                            answer_box.write(answer)
                        elif event == "done":
                            if data.get("notice"):
                                st.warning(data["notice"])
                            sources = data.get("sources", [])
                            answer = data.get("answer", answer)  # the server's redacted final text
                            answer_box.write(answer)
                        elif event == "error":
                            if status_box:
                                status_box.update(label="Error", state="error", expanded=False)
                            st.error(data["message"])
            except requests.exceptions.ReadTimeout:
                if status_box:
                    status_box.update(label="Timeout", state="error", expanded=False)
                st.error("HDFC Loan Bot is still loading or generating. Please wait and try again.")
                st.stop()
            except requests.RequestException:
                st.error("The connection to the assistant was interrupted. Please try again.")
                st.stop()

        if answer:
            if sources:
                _show_sources(sources)
            st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})
