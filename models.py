from sqlalchemy import Column, Integer, String, JSON, DateTime
from datetime import datetime, timezone
from database import Base

class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    # Unique ID for each event row
    id = Column(Integer, primary_key=True, index=True)
    
    # Category/Type of event
    event_type = Column(String, index=True, nullable=False)
    
    # Structured event payload (stored as real JSON)
    payload = Column(JSON, nullable=False)
    
    # Timestamp of when the event hit the database
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))