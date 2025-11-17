from sqlalchemy import Column, Integer, String

from src.models.share_attribute import ShareAttribute
from .base import Base

class TestCase(Base, ShareAttribute):
    __tablename__ = "test_cases"
    test_case_id = Column(Integer, primary_key=True, autoincrement=True)
    case_sheet_id = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    