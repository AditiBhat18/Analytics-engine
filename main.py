from datetime import datetime, timedelta
import hashlib
import hmac
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
from models import AnalyticsEvent, Document, DocumentPermission, User
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

# Database Initialization
Base.metadata.create_all(bind=engine)

# Security Configuration
# Falls back to a default only for local/dev convenience. Set the SECRET_KEY
# environment variable in any shared or production environment.
SECRET_KEY = os.getenv(
    "SECRET_KEY", "SUPER_SECRET_JWT_KEY_CHANGE_THIS_IN_PRODUCTION"
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 Hours

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

app = FastAPI(
    title="Real-Time Collaborative Workspace with Auth & Permissions",
    version="4.0",
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


def check_doc_access(user_id: int, document_id: int, db: Session) -> bool:
    perm = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.document_id == document_id,
            DocumentPermission.user_id == user_id,
        )
        .first()
    )
    return perm is not None


# --- Connection Manager ---
class ConnectionManager:

    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, document_id: int, websocket: WebSocket):
        await websocket.accept()
        if document_id not in self.active_connections:
            self.active_connections[document_id] = []
        self.active_connections[document_id].append(websocket)

    def disconnect(self, document_id: int, websocket: WebSocket):
        if (
            document_id in self.active_connections
            and websocket in self.active_connections[document_id]
        ):
            self.active_connections[document_id].remove(websocket)
            if not self.active_connections[document_id]:
                del self.active_connections[document_id]

    async def broadcast(self, document_id: int, message: dict, sender: WebSocket):
        if document_id in self.active_connections:
            for connection in self.active_connections[document_id]:
                if connection != sender:
                    await connection.send_json(message)

    def get_active_count(self, document_id: int) -> int:
        return len(self.active_connections.get(document_id, []))


manager = ConnectionManager()


# --- Pydantic Schemas ---
class AuthSchema(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def email_not_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Email is required")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, v):
        if not v:
            raise ValueError("Password is required")
        return v


class ShareDocSchema(BaseModel):
    doc_id: int
    target_user_email: str

    @field_validator("target_user_email")
    @classmethod
    def email_not_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Target email is required")
        return v.lower()


class DocumentCreateSchema(BaseModel):
    doc_name: str
    content: Optional[str] = ""

    @field_validator("doc_name")
    @classmethod
    def name_not_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Document name is required")
        if len(v) > 200:
            raise ValueError("Document name is too long")
        return v


# --- UI Route ---
@app.get("/", response_class=HTMLResponse)
def get_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------
# AUTHENTICATION ENDPOINTS
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
    result = []
    for perm in permissions:
        doc = db.query(Document).filter(Document.id == perm.document_id).first()
        if doc:
            result.append(
                {
                    "id": doc.id,
                    "name": doc.name,
                    "is_owner": doc.owner_id == user.id,
                }
            )
    # Stable ordering so the list doesn't jump around between refreshes
    result.sort(key=lambda d: d["id"])
    return result


@app.post("/api/documents")
def create_document(
    doc: DocumentCreateSchema,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    user = get_current_user_from_token(token, db)

    # Every document gets its own unique id, regardless of what name is
    # chosen, so two different documents can never collide or overwrite
    # each other just because they share a display name.
    new_doc = Document(name=doc.doc_name, owner_id=user.id)
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)

    perm = DocumentPermission(document_id=new_doc.id, user_id=user.id)
    db.add(perm)

    db_event = AnalyticsEvent(document_id=new_doc.id, payload=doc.content)
    db.add(db_event)

    db.commit()
    return {
        "message": f"Document '{new_doc.name}' created",
        "doc_id": new_doc.id,
        "doc_name": new_doc.name,
    }


@app.get("/api/documents/{doc_id}")
def get_document(
    doc_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    user = get_current_user_from_token(token, db)
    if not check_doc_access(user.id, doc_id, db):
        raise HTTPException(
            status_code=403, detail="Access denied to this private document"
        )

    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    event = (
        db.query(AnalyticsEvent)
        .filter(AnalyticsEvent.document_id == doc_id)
        .order_by(AnalyticsEvent.id.desc())
        .first()
    )
    content = event.payload if event else ""
    words = len(content.split()) if content else 0
    chars = len(content)
    lines = len(content.splitlines()) if content else 0

    return {
        "doc_id": document.id,
        "doc_name": document.name,
        "is_owner": document.owner_id == user.id,
        "text": content,
        "analytics": {"words": words, "chars": chars, "lines": lines},
    }


@app.delete("/api/documents/{doc_id}")
def delete_document(
    doc_id: int,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Owners deleting their document removes it entirely, for everyone it was
    shared with. Non-owners "deleting" it only removes their OWN access
    (i.e. they leave it) — it stays completely intact for the owner and
    everyone else it's shared with. This is the fix for: "if B deletes a
    file, only B's access should be removed, not affect others."
    """
    user = get_current_user_from_token(token, db)

    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not check_doc_access(user.id, doc_id, db):
        raise HTTPException(
            status_code=403, detail="You do not have access to this document"
        )

    if document.owner_id == user.id:
        db.query(DocumentPermission).filter(
            DocumentPermission.document_id == doc_id
        ).delete()
        db.query(AnalyticsEvent).filter(
            AnalyticsEvent.document_id == doc_id
        ).delete()
        db.delete(document)
        db.commit()
        return {"message": f"Document '{document.name}' deleted for everyone"}
    else:
        db.query(DocumentPermission).filter(
            DocumentPermission.document_id == doc_id,
            DocumentPermission.user_id == user.id,
        ).delete()
        db.commit()
        return {"message": f"You have left '{document.name}'"}


@app.post("/api/documents/share")
def share_document(
    share_data: ShareDocSchema,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    current_user = get_current_user_from_token(token, db)

    document = db.query(Document).filter(Document.id == share_data.doc_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if not check_doc_access(current_user.id, share_data.doc_id, db):
        raise HTTPException(
            status_code=403, detail="You do not have permission to share this file"
        )

    if share_data.target_user_email == current_user.email:
        raise HTTPException(
            status_code=400, detail="You already have access to this document"
        )

    target_user = (
        db.query(User).filter(User.email == share_data.target_user_email).first()
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user email not found")

    existing_perm = (
        db.query(DocumentPermission)
        .filter(
            DocumentPermission.document_id == share_data.doc_id,
            DocumentPermission.user_id == target_user.id,
        )
        .first()
    )
    if not existing_perm:
        new_perm = DocumentPermission(
            document_id=share_data.doc_id, user_id=target_user.id
        )
        db.add(new_perm)
        db.commit()

    return {
        "message": (
            f"Document '{document.name}' shared with"
            f" {share_data.target_user_email}"
        )
    }


# ---------------------------------------------------------
# PROTECTED WEBSOCKET CHANNEL
# ---------------------------------------------------------
@app.websocket("/ws/{doc_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    doc_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        user = get_current_user_from_token(token, db)
    except HTTPException:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if not check_doc_access(user.id, doc_id, db):
        await websocket.close(code=4003, reason="Access Denied")
        return

    await manager.connect(doc_id, websocket)
    await manager.broadcast(
        doc_id,
        {"type": "USER_COUNT", "count": manager.get_active_count(doc_id)},
        sender=None,
    )

    try:
        while True:
            data = await websocket.receive_json()
            content = data.get("text", "")

            words = len(content.split()) if content else 0
            chars = len(content)
            lines = len(content.splitlines()) if content else 0

            db_event = AnalyticsEvent(document_id=doc_id, payload=content)
            db.add(db_event)
            db.commit()

            await manager.broadcast(
                doc_id,
                {
                    "type": "TEXT_UPDATE",
                    "text": content,
                    "analytics": {"words": words, "chars": chars, "lines": lines},
                },
                sender=websocket,
            )

    except WebSocketDisconnect:
        manager.disconnect(doc_id, websocket)
        await manager.broadcast(
            doc_id,
            {"type": "USER_COUNT", "count": manager.get_active_count(doc_id)},
            sender=None,
        )