from sqlalchemy import Column, Integer, String, Text

from .base import Base
from .share_attribute import ShareAttribute

class KnowledgeFile(Base, ShareAttribute):
    __tablename__ = "knowledge_file"
    knowledge_file_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False)
    name = Column(String(255))
    description = Column(Text)