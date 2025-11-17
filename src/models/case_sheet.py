from sqlalchemy import Column, Integer, String, Text

from .base import Base
from .share_attribute import ShareAttribute

class CaseSheet(Base, ShareAttribute):
    __tablename__ = "case_sheet"
    __table_args__ = {'schema': 'qa_test'}
    
    case_sheet_id = Column(Integer, primary_key=True, autoincrement=True)
    case_file_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
