import json
import os
import uuid

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000/ask/stream")
MAX_BACKEND_HISTORY = 12

st.set_page_config(page_title="LoanBot - EXL Banking Assistant", layout="wide", initial_sidebar_state="collapsed")
st.title("LoanBot - EXL Banking Assistant")
st.caption("Ask me anything about loan eligibility")
st.divider()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if message["role"] == "assistant" and message.get("sources"):
            with st.expander("View Sources"):
                for source in message["sources"]:
                    st.caption(f"{source['document']} - page {source['page']}")
                    st.write(source["excerpt"])

prompt = st.chat_input("Ask about loan eligibility...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        answer_box = st.empty()
        answer = ""
        sources = []
        history_for_api = st.session_state.messages[-MAX_BACKEND_HISTORY:]
        try:
            payload = {
                "question": prompt,
                "history": history_for_api,
                "session_id": st.session_state.session_id,
            }
            with requests.post(API_URL, json=payload, stream=True, timeout=(5, 300)) as response:
                if response.status_code == 422:
                    st.error("Request format was rejected by the backend.")
                    st.code(response.text)
                    st.stop()
                response.raise_for_status()
                event = ""
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        payload = json.loads(line.removeprefix("data: "))
                        if event == "token":
                            answer += payload["text"]
                            answer_box.write(answer)
                        elif event == "done":
                            sources = payload.get("sources", [])
                        elif event == "error":
                            st.error(payload["message"])
        except requests.exceptions.ReadTimeout:
            st.error("LoanBot is still loading or generating with the local model. Please wait and try again.")
            st.stop()
        except requests.exceptions.RequestException as error:
            st.error("Backend is unavailable. Start FastAPI and Ollama, then try again.")
            st.caption(str(error))
            st.stop()

        if answer:
            if sources:
                with st.expander("View Sources"):
                    for source in sources:
                        st.caption(f"{source['document']} - page {source['page']}")
                        st.write(source["excerpt"])
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "sources": sources,
            })
