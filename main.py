import asyncio
from datetime import datetime, timedelta
import hashlib
import hmac
import os
from typing import Optional
from database import Base, engine, get_db, SessionLocal
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
from pydantic import BaseModel, EmailStr, field_validator
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
    version="5.0",
)


DEBOUNCE_SECONDS = 2

pending_content: dict[int, str] = {}
save_tasks: dict[int, asyncio.Task] = {}


async def _flush_document(doc_id: int) -> None:
    """Persist the buffered content for doc_id, if any is pending."""
    content = pending_content.pop(doc_id, None)
    save_tasks.pop(doc_id, None)
    if content is None:
        return

   
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == doc_id).first()
        if document is None:
            # Document was deleted while an edit was still pending; nothing
            # left to save it to.
            return
        document.content = content
        db.add(AnalyticsEvent(document_id=doc_id, payload=content))
        db.commit()
    finally:
        db.close()


async def _debounced_flush(doc_id: int) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        # A newer keystroke rescheduled this before it could fire; the
        # newer task will handle saving the latest content instead.
        return
    await _flush_document(doc_id)


def _schedule_flush(doc_id: int) -> None:
    existing = save_tasks.get(doc_id)
    if existing and not existing.done():
        existing.cancel()
    save_tasks[doc_id] = asyncio.create_task(_debounced_flush(doc_id))


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
class _BaseAuthSchema(BaseModel):
    """
    Shared email handling for login/register: EmailStr enforces a real
    email format (must have an "@", a valid domain shape, etc.) instead of
    accepting any non-blank string like "abcd". Whitespace is trimmed
    before format-checking, and the result is lowercased so the same
    address always matches regardless of how it was typed/cased.
    """
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v):
        return v.lower()


class RegisterSchema(_BaseAuthSchema):
    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if not v:
            raise ValueError("Password is required")
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class LoginSchema(_BaseAuthSchema):
    @field_validator("password")
    @classmethod
    def password_not_blank(cls, v):
        # Intentionally does NOT enforce a minimum length here: an account
        # created before this validation existed may have a shorter
        # password, and login must keep working for it. Only new
        # registrations (RegisterSchema) are held to the length rule.
        if not v:
            raise ValueError("Password is required")
        return v


class ShareDocSchema(BaseModel):
    doc_id: int
    target_user_email: EmailStr

    @field_validator("target_user_email", mode="before")
    @classmethod
    def strip_target_email(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("target_user_email")
    @classmethod
    def lowercase_target_email(cls, v):
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
def register(user_data: RegisterSchema, db: Session = Depends(get_db)):
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
def login(login_data: LoginSchema, db: Session = Depends(get_db)):
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
    # each other just because they share a display name. Its initial
    # content is stored directly on the row.
    new_doc = Document(
        name=doc.doc_name, owner_id=user.id, content=doc.content or ""
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)

    perm = DocumentPermission(document_id=new_doc.id, user_id=user.id)
    db.add(perm)
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

    # Prefer any not-yet-flushed in-memory content over what's on disk, so a
    # reload always shows the very latest edits even mid-debounce-window --
    # this is what keeps the user-visible behavior identical to before.
    content = pending_content.get(doc_id, document.content or "")
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
    (i.e. they leave it) -- it stays completely intact for the owner and
    everyone else it's shared with.
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

        # Drop any unsaved buffer/pending timer for this now-deleted document.
        pending_content.pop(doc_id, None)
        existing_task = save_tasks.pop(doc_id, None)
        if existing_task and not existing_task.done():
            existing_task.cancel()

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

            # Update the in-memory buffer and broadcast to peers instantly --
            # no DB write on this hot path. The actual persistence is
            # scheduled for DEBOUNCE_SECONDS after the last keystroke on
            # this document (see _schedule_flush at the top of this file).
            pending_content[doc_id] = content
            _schedule_flush(doc_id)

            words = len(content.split()) if content else 0
            chars = len(content)
            lines = len(content.splitlines()) if content else 0

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
        # If nobody is left editing this document, don't wait out the full
        # debounce window -- flush any unsaved edits immediately so closing
        # the last open tab can never silently lose data.
        if manager.get_active_count(doc_id) == 0:
            existing_task = save_tasks.get(doc_id)
            if existing_task and not existing_task.done():
                existing_task.cancel()
            await _flush_document(doc_id)