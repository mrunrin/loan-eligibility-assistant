from fastapi import FastAPI
from langfuse import observe
from pydantic import BaseModel

from app.rag import answer_question
from app.tracing import langfuse

app = FastAPI(title="Loan Eligibility Assistant")

class Message(BaseModel):
    role: str
    content: str

class AskRequest(BaseModel):
    question: str
    history: list[Message] = []


@app.get("/")
def health_check():
    return {"message":"Loan Eligibility Assistant is running"}

@app.post("/ask")
@observe(name="loan-eligibility-ask")
def ask(request: AskRequest):
    result = answer_question(
    question=request.question,
    history=[message.model_dump() for message in request.history],
)

    try:
        langfuse.update_current_span(input=request.question, output=result)
        langfuse.flush()
    except Exception as error:
        print(f"Langfuse tracing skipped: {error}")

    return result

