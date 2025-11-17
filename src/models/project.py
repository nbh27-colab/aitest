from sqlalchemy import Column, DateTime, Integer, String, Text, func

from .base import Base


class Project(Base):
    __tablename__ = "project"
    project_id = Column(Integer, primary_key=True, autoincrement=True)
    created_by_id = Column(Integer, nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_time = Column(DateTime(timezone=True), server_default=func.now())
