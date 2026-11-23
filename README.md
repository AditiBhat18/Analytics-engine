
# Analytics Engine — Real-Time Collaborative Workspace

A full-stack collaborative document management application built with **FastAPI, PostgreSQL, and JavaScript**. It provides secure user authentication, document ownership, sharing permissions, and collaborative editing.

## Features

- **Authentication:** JWT-based authentication with secure password hashing.
- **Document Management:** Create, access, edit, and delete documents.
- **Access Control:** Document ownership and user-specific sharing permissions.
- **Real-Time Collaboration:** WebSocket-based document synchronization and live analytics during local development.
- **Live Analytics:** Word, character, line, and active-user counts.
- **Collision-Safe Design:** Unique document IDs prevent conflicts between documents with identical names.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Authentication | JWT, PBKDF2-HMAC-SHA256 |
| Real-Time Communication | WebSockets |
| Frontend | HTML, CSS, JavaScript |
| Local Database | Docker Compose |
| Deployment | Vercel |
| Hosted Database | Supabase |

## Project Structure

| File | Description |
|---|---|
| `main.py` | FastAPI application, API endpoints, authentication, and WebSocket handling |
| `database.py` | Database engine and session configuration |
| `models.py` | Relational database models |
| `index.html` | Frontend interface and client-side functionality |
| `docker-compose.yml` | Local PostgreSQL database configuration |
| `requirements.txt` | Python dependencies |

## Getting Started

### Prerequisites
- Python 3.10+
- Docker Desktop with Docker Compose
- Git

### 1. Clone the repository

```bash
git clone https://github.com/AditiBhat18/Analytics-engine.git
cd Analytics-engine
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://supply_user:your_password@localhost:5432/analytics_engine
SECRET_KEY=your_secure_secret_key

POSTGRES_DB=analytics_engine
POSTGRES_USER=supply_user
POSTGRES_PASSWORD=your_password
```

Use matching PostgreSQL credentials in your Docker Compose configuration. Keep `.env` private and never commit secrets.

### 3. Start PostgreSQL

```bash
docker compose up -d
```

### 4. Install dependencies

```bash
py -m venv .venv
```

Activate the environment and install dependencies:

```bash
pip install -r requirements.txt
```

### 5. Start the application

```bash
uvicorn main:app --reload
```

### 6. Open the application

Visit:

`http://localhost:8000`

For local collaboration testing, open the application in two browser sessions and access the same shared document.

## Deployment

- **Application:** Deployed on Vercel.
- **Database:** Hosted on Supabase PostgreSQL.
- **Configuration:** Deployment secrets and database credentials are managed through environment variables.

Note: The current WebSocket implementation uses in-memory connection management and is intended for local development. Persistent real-time collaboration is not guaranteed in Vercel's serverless environment.

## Future Improvements

- Operational Transformation or CRDT-based concurrent editing.
- Document version history and rollback.
- Rich-text editing.
- Persistent real-time collaboration across distributed instances.
- Rate limiting and additional security protections.
