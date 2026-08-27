# from database import Base
# from sqlalchemy import Column, ForeignKey, Integer, String, Text
# from sqlalchemy.orm import relationship


# class User(Base):
#   __tablename__ = "users"

#   id = Column(Integer, primary_key=True, index=True)
#   email = Column(String, unique=True, index=True, nullable=False)
#   hashed_password = Column(String, nullable=False)


# class DocumentPermission(Base):
#   __tablename__ = "document_permissions"

#   id = Column(Integer, primary_key=True, index=True)
#   doc_name = Column(String, index=True, nullable=False)
#   user_id = Column(Integer, ForeignKey("users.id"), nullable=False)


# class AnalyticsEvent(Base):
#   __tablename__ = "analytics_events"

#   id = Column(Integer, primary_key=True, index=True)
#   event_type = Column(String, index=True, nullable=False)
#   payload = Column(Text, nullable=True)

from database import Base
from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship


class User(Base):
  __tablename__ = "users"

  id = Column(Integer, primary_key=True, index=True)
  email = Column(String, unique=True, index=True, nullable=False)
  hashed_password = Column(String, nullable=False)


class DocumentPermission(Base):
  __tablename__ = "document_permissions"

  id = Column(Integer, primary_key=True, index=True)
  doc_name = Column(String, index=True, nullable=False)
  user_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class AnalyticsEvent(Base):
  __tablename__ = "analytics_events"

  id = Column(Integer, primary_key=True, index=True)
  event_type = Column(String, index=True, nullable=False)
  payload = Column(Text, nullable=True)