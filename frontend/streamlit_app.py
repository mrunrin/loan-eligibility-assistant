import requests
import streamlit as st

API_URL = "http://localhost:8000/ask"

st.set_page_config(
    page_title="LoanBot - EXL Banking Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

header = st.container()
chat_area = st.container()
input_area = st.container()

with header:
    st.title("🤖 LoanBot - EXL Banking Assistant")
    st.caption("Ask me anything about loan eligibility")
    st.divider()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if message["role"] == "assistant" and message.get("source"):
            with st.expander("View Sources"):
                st.write(message["source"])

prompt = st.chat_input("Ask about loan eligibility...")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        with st.spinner("LoanBot is checking the policy..."):
            response = requests.post(API_URL, json={"question": prompt,"history": st.session_state.messages}, timeout=120)

        if response.ok:
            data = response.json()
            answer = data["answer"]
            source = data["source"]

            st.write(answer)
            with st.expander("View Sources"):
                st.write(source)

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "source": source,
            })
        else:
            st.error("API request failed.")
            st.write(response.text)