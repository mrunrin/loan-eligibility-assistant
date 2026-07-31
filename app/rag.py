from functools import lru_cache

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import CHAT_MODEL, EMBEDDING_MODEL, OLLAMA_BASE_URL

PDF_PATH = "data/loan_eligibility.pdf"
VECTORSTORE_PATH = "vectorstore"


@lru_cache(maxsize=1)
def retriever():
    loader = PyPDFLoader(PDF_PATH)
    documents = loader.load()

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)
    chunks = splitter.split_documents(documents)

    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=VECTORSTORE_PATH,
    )

    return vectorstore.as_retriever(search_kwargs={"k": 3})


def classify_question(question: str, history: list[dict]) -> str:
    history_text = "\n".join(
        f"{message.get('role')}: {message.get('content')}"
        for message in history[-10:]
    )

    llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL)

    prompt = f"""
You are a message router for a banking assistant.

Return exactly one label:
small_talk
memory
policy
unsupported

Rules:
- Return small_talk for greetings, thanks, or casual friendly messages.
- Return memory if the user asks about anything mentioned earlier in the conversation.
- Return memory if the user shares personal/session details like name, role, location, goal, preference, or project status.
- Return policy if the user asks about loan eligibility, income, credit score, documents, age, tenure, debt-to-income ratio, or banking policy.
- Return unsupported only if the message is unrelated and not useful to remember.

Conversation history:
{history_text}

User question:
{question}

Label:
"""
    label = llm.invoke(prompt).content.strip().lower()
    return label.split()[0].replace(":", "")


def answer_memory_question(question: str, conversation_context: str) -> dict:
    llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL)

    prompt = f"""
Answer the user's question using only the conversation history.
If the answer is not present in the conversation history, say so politely.

Conversation history:
{conversation_context}

User question:
{question}
"""

    response = llm.invoke(prompt)

    return {
        "answer": response.content,
        "source": "conversation history",
    }


def answer_question(question: str, history: list[dict] | None = None) -> dict:
    history = history or []

    recent_history = history[-10:]
    conversation_context = "\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in recent_history
    )

    question_type = classify_question(question, history)

    if question_type == "small_talk":
        return {
            "answer": "Hey, good to see you. Ask me anything about loan eligibility, income rules, credit score, documents, or tenure.",
            "source": "general assistant response",
        }

    if question_type == "memory":
        return answer_memory_question(question, conversation_context + f"\nuser: {question}")
    if question_type == "unsupported":
        return {
            "answer": "I can help with loan eligibility questions, income rules, credit score, documents, tenure, and details you shared in this chat.",
            "source": "general assistant response",
        }

    retrievers = retriever()
    docs = retrievers.invoke(question)

    context = "\n\n".join(doc.page_content for doc in docs)

    prompt = f"""
You are LoanBot, a helpful banking loan eligibility assistant.

Use the policy context to answer loan eligibility questions.
If the answer is not in the policy context, say politely that you could not find that detail in the loan eligibility policy.

Do not say "there is no context" or sound robotic.
Keep the answer clear and short.

Policy context:
{context}

User question:
{question}
"""

    llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL)
    response = llm.invoke(prompt)

    return {
        "answer": response.content,
        "source": "data/loan_eligibility.pdf",
    }