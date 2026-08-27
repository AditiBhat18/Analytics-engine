from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
from typing import Optional
from database import Base, engine, get_db
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from models import AnalyticsEvent, DocumentPermission, User
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Database Initialization
Base.metadata.create_all(bind=engine)

# Security Configuration
SECRET_KEY = "SUPER_SECRET_JWT_KEY_CHANGE_THIS_IN_PRODUCTION"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 Hours

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

app = FastAPI(
    title="Real-Time Collaborative Workspace with Auth & Permissions",
    version="3.0",
)


# --- Pure PBKDF2 Password Hashing ---
def get_password_hash(password: str) -> str:
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 100000
    )
    return f"{salt.hex()}${pwd_hash.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt_hex, hash_hex = hashed_password.split("$")
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        new_hash = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, 100000
        )
        return hmac.compare_digest(new_hash, expected_hash)
    except Exception:
        return False


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user_from_token(
    token: str, db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user


def check_doc_access(user_id: int, doc_name: str, db: Session) -> bool:
    perm = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.doc_name == doc_name,
            DocumentPermission.user_id == user_id,
        )
        .first()
    )
    return perm is not None


# --- Connection Manager ---
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


# --- Pydantic Schemas ---
class AuthSchema(BaseModel):
    email: str
    password: str


class ShareDocSchema(BaseModel):
    doc_name: str
    target_user_email: str


class DocumentCreateSchema(BaseModel):
    doc_name: str
    content: Optional[str] = ""


# --- UI Route ---
@app.get("/", response_class=HTMLResponse)
def get_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------
# AUTHENTICATION ENDPOINTS (BOTH JSON BASED)
# ---------------------------------------------------------
@app.post("/api/auth/register")
def register(user_data: AuthSchema, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pwd = get_password_hash(user_data.password)
    new_user = User(email=user_data.email, hashed_password=hashed_pwd)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created successfully", "email": new_user.email}


@app.post("/api/auth/login")
def login(login_data: AuthSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == login_data.email).first()
    if not user:
        raise HTTPException(
            status_code=400, detail="Incorrect email or password"
        )

    is_valid = verify_password(login_data.password, user.hashed_password)

    if not is_valid:
        raise HTTPException(
            status_code=400, detail="Incorrect email or password"
        )

    access_token = create_access_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "email": user.email,
    }


# ---------------------------------------------------------
# PROTECTED DOCUMENT & SHARING ENDPOINTS
# ---------------------------------------------------------
@app.get("/api/documents")
def list_user_documents(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    user = get_current_user_from_token(token, db)
    permissions = (
        db.query(DocumentPermission)
        .filter(DocumentPermission.user_id == user.id)
        .all()
    )
    doc_names = [p.doc_name for p in permissions]
    return doc_names


@app.post("/api/documents")
def create_document(
    doc: DocumentCreateSchema,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    user = get_current_user_from_token(token, db)

    perm = DocumentPermission(doc_name=doc.doc_name, user_id=user.id)
    db.add(perm)

    event_type = f"DOC_{doc.doc_name}"
    db_event = AnalyticsEvent(event_type=event_type, payload=doc.content)
    db.add(db_event)

    db.commit()
    return {
        "message": f"Document '{doc.doc_name}' created",
        "doc_name": doc.doc_name,
    }


@app.get("/api/documents/{doc_name}")
def get_document(
    doc_name: str,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    user = get_current_user_from_token(token, db)
    if not check_doc_access(user.id, doc_name, db):
        raise HTTPException(
            status_code=403, detail="Access denied to this private document"
        )

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


@app.post("/api/documents/share")
def share_document(
    share_data: ShareDocSchema,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    current_user = get_current_user_from_token(token, db)

    if not check_doc_access(current_user.id, share_data.doc_name, db):
        raise HTTPException(
            status_code=403, detail="You do not have permission to share this file"
        )

    target_user = (
        db.query(User).filter(User.email == share_data.target_user_email).first()
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user email not found")

    existing_perm = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.doc_name == share_data.doc_name,
            DocumentPermission.user_id == target_user.id,
        )
        .first()
    )
    if not existing_perm:
        new_perm = DocumentPermission(
            doc_name=share_data.doc_name, user_id=target_user.id
        )
        db.add(new_perm)
        db.commit()

    return {
        "message": (
            f"Document '{share_data.doc_name}' shared with"
            f" {share_data.target_user_email}"
        )
    }


# ---------------------------------------------------------
# PROTECTED WEBSOCKET CHANNEL
# ---------------------------------------------------------
@app.websocket("/ws/{doc_name}")
async def websocket_endpoint(
    websocket: WebSocket,
    doc_name: str,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user_from_token(token, db)
    except HTTPException:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if not check_doc_access(user.id, doc_name, db):
        await websocket.close(code=4003, reason="Access Denied")
        return

    await manager.connect(doc_name, websocket)
    await manager.broadcast(
        doc_name,
        {"type": "USER_COUNT", "count": manager.get_active_count(doc_name)},
        sender=None,
    )

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("text", "")

            words = len(content.split()) if content else 0
            chars = len(content)
            lines = len(content.splitlines()) if content else 0

            db_event = AnalyticsEvent(
                event_type=f"DOC_{doc_name}", payload=content
            )
            db.add(db_event)
            db.commit()

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