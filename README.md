# Real-Time Collaborative Workspace

A full-stack, real-time collaborative document editor — think a minimal Google Docs — built with **FastAPI**, **PostgreSQL**, and **native WebSockets**. Multiple users can edit the same document simultaneously and see each other's changes, live word/character/line counts, and active user presence update in real time, with no page refresh.


## Features

- **Authentication** — Email/password registration and login using JWT bearer tokens, with PBKDF2-HMAC-SHA256 password hashing (salted, industry-standard, not a stored plaintext or reversible scheme).
- **Real-time collaborative editing** — A WebSocket channel per document broadcasts every keystroke to all other connected clients editing that document, with live text sync.
- **Live analytics** — Word count, character count, line count, and active user count update instantly for every connected user as anyone types — no polling, no refresh.
- **Document ownership & sharing** — Every document has a real owner. Owners can share documents with other registered users by email. Shared users can view and edit but only the owner can permanently delete a document — non-owners can "leave" a shared document without affecting anyone else's access to it.
- **Collision-safe document identity** — Documents are identified by a unique database ID, not by their display name, so two different users can name their documents identically without any risk of one overwriting or gaining access to the other's content.
- **Dockerized database** — PostgreSQL runs in a container via Docker Compose for consistent, reproducible local setup.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| Real-time transport | WebSockets (native, via FastAPI/Starlette) |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Auth | JWT (python-jose), PBKDF2-HMAC password hashing |
| Frontend | Vanilla HTML/CSS/JavaScript (no framework) |
| Containerization | Docker Compose |

## Architecture Overview

- **`main.py`** — FastAPI app: REST endpoints for auth and document CRUD, plus the WebSocket endpoint that manages real-time broadcast per document.
- **`models.py`** — SQLAlchemy models: `User`, `Document` (with `owner_id`), `DocumentPermission` (join table granting per-user access to a document by its real ID), `AnalyticsEvent` (append-only log of document content snapshots, used to reconstruct current content and compute analytics).
- **`database.py`** — SQLAlchemy engine/session setup.
- **`index.html`** — Single-page frontend: login/register, document list sidebar, live-updating editor, and a WebSocket client that syncs text and analytics in real time.
- **`docker-compose.yml`** — Spins up the PostgreSQL container the app connects to.

### Why documents have their own ID instead of just a name

Early in development, documents were identified only by their display name. This meant two different users naming a document the same thing (e.g., both calling something "Notes") would collide — potentially granting one user access to the other's private content. The current design gives every document its own auto-incrementing ID at creation time, completely decoupled from its display name, with an `owner_id` marking who created it. All permissions and content are keyed by this ID, so name collisions are structurally impossible.

## Getting Started

### Prerequisites
- Python 3.10+
- Docker & Docker Compose
- pip

### 1. Clone the repo
```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>
```

### 2. Start the database
```bash
docker-compose up -d
```

### 3. Install Python dependencies
```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary "python-jose[cryptography]" pydantic "uvicorn[standard]"
```

### 4. Run the app
```bash
uvicorn main:app --reload
```

### 5. Open it
Go to **http://localhost:8000** in your browser.

To test real-time multi-user collaboration: open a second tab (or an incognito/private window) and log in as a different user — each tab keeps its own session independently, so you can watch both users edit the same shared document live.

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/register` | Create a new user account |
| POST | `/api/auth/login` | Log in, returns a JWT |
| GET | `/api/documents` | List documents you own or have been shared |
| POST | `/api/documents` | Create a new document |
| GET | `/api/documents/{doc_id}` | Get a document's current content and analytics |
| DELETE | `/api/documents/{doc_id}` | Delete (if owner) or leave (if shared with you) |
| POST | `/api/documents/share` | Share a document with another user by email |
| WS | `/ws/{doc_id}` | Real-time text + analytics + presence channel for a document |

## Possible Future Improvements

- Operational-transform or CRDT-based merging for true concurrent multi-cursor editing (currently last-write-wins per keystroke broadcast)
- Rich text formatting instead of plain text
- Document version history / rollback
- Rate limiting on auth endpoints

## License

MIT — feel free to use this as a learning reference.