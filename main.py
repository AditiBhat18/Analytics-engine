from typing import Optional
from fastapi import FastAPI, Depends
from sqlmodel import Field, SQLModel, create_engine, Session

class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str

DATABASE_URL = "postgresql://admin:supersecretpassword@localhost:5432/analytics_engine"
engine = create_engine(DATABASE_URL)

app = FastAPI(title="Collaborative Analytics Engine API")

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

@app.post("/documents/create")
def create_document(doc: Document, session: Session = Depends(get_session)):
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc