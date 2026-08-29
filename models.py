from database import Base
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)


class Document(Base):
    """
    A document is identified by its own auto-incrementing `id`, NOT by its
    display `name`. This is what makes the owner concept and access control
    actually safe: two different users can each name a document "Notes"
    without colliding, because they get two different Document rows with
    two different ids. `owner_id` records who created it and is the only
    user allowed to delete it entirely.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class DocumentPermission(Base):
    """
    One row per (document, user) pair that has access. References the
    document's real id, not its name, so permissions can never leak across
    two different documents that happen to share a display name.
    """
    __tablename__ = "document_permissions"
    __table_args__ = (
        UniqueConstraint("document_id", "user_id", name="uq_document_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class AnalyticsEvent(Base):
    """
    Each edit is stored as a new event row (append-only log), keyed by the
    document's real id. The most recent event for a document_id is treated
    as its current content.
    """
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    payload = Column(Text, nullable=True)