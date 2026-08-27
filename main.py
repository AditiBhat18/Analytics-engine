import json
from typing import Optional
from database import Base, engine, get_db
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from models import AnalyticsEvent
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

# Initialize database schema in PostgreSQL
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Real-Time Collaborative Workspace & Analytics Engine",
    version="2.0",
)


# --- Connection Manager for Multi-Document WebSocket Streaming ---
class ConnectionManager:

  def __init__(self):
    self.active_connections: dict[str, list[WebSocket]] = {}

  async def connect(self, doc_name: str, websocket: WebSocket):
    await websocket.accept()
    if doc_name not in self.active_connections:
      self.active_connections[doc_name] = []
    self.active_connections[doc_name].append(websocket)

  def disconnect(self, doc_name: str, websocket: WebSocket):
    if (
        doc_name in self.active_connections
        and websocket in self.active_connections[doc_name]
    ):
      self.active_connections[doc_name].remove(websocket)

  async def broadcast(self, doc_name: str, message: dict, sender: WebSocket):
    if doc_name in self.active_connections:
      for connection in self.active_connections[doc_name]:
        if connection != sender:
          await connection.send_json(message)

  def get_active_count(self, doc_name: str) -> int:
    return len(self.active_connections.get(doc_name, []))


manager = ConnectionManager()


# --- Pydantic Schemas for CRUD ---
class DocumentCreateSchema(BaseModel):
  doc_name: str
  content: Optional[str] = ""


class DocumentUpdateSchema(BaseModel):
  content: str


class RawEventUpdateSchema(BaseModel):
  event_type: Optional[str] = None
  payload: Optional[dict] = None


# ---------------------------------------------------------
# 1. UI ROUTE
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def get_ui():
  with open("index.html", "r", encoding="utf-8") as f:
    return f.read()


# ---------------------------------------------------------
# 2. FILE MANAGEMENT & DOCUMENT CRUD ENDPOINTS
# ---------------------------------------------------------


# READ: Get all saved document names
@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
  results = (
      db.query(AnalyticsEvent.event_type)
      .filter(AnalyticsEvent.event_type.like("DOC_%"))
      .distinct()
      .all()
  )
  doc_names = [r[0].replace("DOC_", "") for r in results]
  if "default" not in doc_names:
    doc_names.append("default")
  return doc_names


# CREATE: Create a new document explicitly via REST
@app.post("/api/documents")
def create_document(doc: DocumentCreateSchema, db: Session = Depends(get_db)):
  event_type = f"DOC_{doc.doc_name}"
  db_event = AnalyticsEvent(event_type=event_type, payload=doc.content)
  db.add(db_event)
  db.commit()
  return {
      "message": f"Document '{doc.doc_name}' created successfully",
      "doc_name": doc.doc_name,
  }


# READ: Get document content and real-time metrics
@app.get("/api/documents/{doc_name}")
def get_document(doc_name: str, db: Session = Depends(get_db)):
  event = (
      db.query(AnalyticsEvent)
      .filter(AnalyticsEvent.event_type == f"DOC_{doc_name}")
      .order_by(AnalyticsEvent.id.desc())
      .first()
  )
  content = event.payload if event else ""
  words = len(content.split()) if content else 0
  chars = len(content)
  lines = len(content.splitlines()) if content else 0

  return {
      "doc_name": doc_name,
      "text": content,
      "analytics": {"words": words, "chars": chars, "lines": lines},
  }


# UPDATE: Update document content explicitly via REST
@app.put("/api/documents/{doc_name}")
def update_document(
    doc_name: str, doc_update: DocumentUpdateSchema, db: Session = Depends(get_db)
):
  event_type = f"DOC_{doc_name}"
  db_event = AnalyticsEvent(event_type=event_type, payload=doc_update.content)
  db.add(db_event)
  db.commit()
  return {
      "message": f"Document '{doc_name}' updated successfully",
      "doc_name": doc_name,
  }


# DELETE: Delete document and all historical revisions
@app.delete("/api/documents/{doc_name}")
def delete_document(doc_name: str, db: Session = Depends(get_db)):
  event_type = f"DOC_{doc_name}"
  records = (
      db.query(AnalyticsEvent)
      .filter(AnalyticsEvent.event_type == event_type)
      .all()
  )
  if not records:
    raise HTTPException(status_code=404, detail="Document not found")

  for record in records:
    db.delete(record)
  db.commit()
  return {
      "message": (
          f"Document '{doc_name}' and its analytics history deleted successfully"
      )
  }


# ---------------------------------------------------------
# 3. GLOBAL ANALYTICS ENDPOINTS
# ---------------------------------------------------------


# Summary Analytics: Total system events and active documents
@app.get("/api/analytics/summary")
def get_analytics_summary(db: Session = Depends(get_db)):
  total_events = db.query(func.count(AnalyticsEvent.id)).scalar() or 0
  unique_types = (
      db.query(func.count(func.distinct(AnalyticsEvent.event_type))).scalar()
      or 0
  )
  return {
      "total_events_logged": total_events,
      "unique_event_types": unique_types,
  }


# Event Distribution Analytics
@app.get("/api/analytics/event-types")
def get_event_type_distribution(
    limit: int = 10, db: Session = Depends(get_db)
):
  results = (
      db.query(
          AnalyticsEvent.event_type, func.count(AnalyticsEvent.id).label("count")
      )
      .group_by(AnalyticsEvent.event_type)
      .order_by(func.count(AnalyticsEvent.id).desc())
      .limit(limit)
      .all()
  )
  return [
      {"event_type": event_type, "count": count} for event_type, count in results
  ]


# ---------------------------------------------------------
# 4. RAW DATABASE RECORD CRUD (For System Admin / Testing)
# ---------------------------------------------------------


# READ: View raw audit log events
@app.get("/api/events")
def get_all_raw_events(limit: int = 50, db: Session = Depends(get_db)):
  return (
      db.query(AnalyticsEvent)
      .order_by(AnalyticsEvent.id.desc())
      .limit(limit)
      .all()
  )


# UPDATE: Modify raw log record by ID
@app.put("/api/events/{event_id}")
def update_raw_event(
    event_id: int,
    event_update: RawEventUpdateSchema,
    db: Session = Depends(get_db),
):
  event = (
      db.query(AnalyticsEvent).filter(AnalyticsEvent.id == event_id).first()
  )
  if not event:
    raise HTTPException(status_code=404, detail="Event log not found")

  if event_update.event_type:
    event.event_type = event_update.event_type
  if event_update.payload is not None:
    event.payload = json.dumps(event_update.payload)

  db.commit()
  db.refresh(event)
  return event


# DELETE: Wipe raw event log by ID
@app.delete("/api/events/{event_id}")
def delete_raw_event(event_id: int, db: Session = Depends(get_db)):
  event = (
      db.query(AnalyticsEvent).filter(AnalyticsEvent.id == event_id).first()
  )
  if not event:
    raise HTTPException(status_code=404, detail="Event log not found")

  db.delete(event)
  db.commit()
  return {"message": f"Raw event {event_id} wiped from PostgreSQL"}


# ---------------------------------------------------------
# 5. REAL-TIME WEBSOCKET STREAMING ENGINE
# ---------------------------------------------------------
@app.websocket("/ws/{doc_name}")
async def websocket_endpoint(
    websocket: WebSocket, doc_name: str, db: Session = Depends(get_db)
):
  await manager.connect(doc_name, websocket)

  # Notify all connected tabs of updated active user count
  await manager.broadcast(
      doc_name,
      {"type": "USER_COUNT", "count": manager.get_active_count(doc_name)},
      sender=None,
  )

  try:
    while True:
      data = await websocket.receive_json()
      content = data.get("text", "")

      # Calculate Real-Time Document Analytics
      words = len(content.split()) if content else 0
      chars = len(content)
      lines = len(content.splitlines()) if content else 0

      # Persist state into PostgreSQL
      db_event = AnalyticsEvent(
          event_type=f"DOC_{doc_name}", payload=content
      )
      db.add(db_event)
      db.commit()

      # Broadcast live text change and analytics to all concurrent collaborators
      await manager.broadcast(
          doc_name,
          {
              "type": "TEXT_UPDATE",
              "text": content,
              "analytics": {"words": words, "chars": chars, "lines": lines},
          },
          sender=websocket,
      )

  except WebSocketDisconnect:
    manager.disconnect(doc_name, websocket)
    await manager.broadcast(
        doc_name,
        {"type": "USER_COUNT", "count": manager.get_active_count(doc_name)},
        sender=None,
    )