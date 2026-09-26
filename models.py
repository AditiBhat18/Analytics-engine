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
    display `name`, so two users can never collide by naming a document the
    same thing. `owner_id` records who created it (only they can delete it
    entirely). `content` holds the current text directly on this row, so
    reading a document's content is a single fast lookup with no extra
    query needed.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False, default="")


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
    A coarse-grained edit-history log. Unlike before, this is NO LONGER
    written on every keystroke — only once per debounced save (i.e. a
    couple seconds after someone stops typing), so its row count stays
    small. The authoritative current content lives on Document.content;
    this table is purely a low-frequency history/audit trail.
    """
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    payload = Column(Text, nullable=True)