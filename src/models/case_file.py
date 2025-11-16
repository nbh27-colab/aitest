from sqlalchemy import Column, Integer, String, Text

from src.models.share_attribute import ShareAttribute
from src.models.base import Base

class CaseFile(Base, ShareAttribute):
    __tablename__ = "case_file"
    case_file_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    file_path = Column(String(1024), nullable=True)
    login_info_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    