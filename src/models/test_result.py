from sqlalchemy import Column, Integer, String, Boolean

from .share_attribute import ShareAttribute
from .base import Base


class TestResult(Base, ShareAttribute):
    __tablename__ = "test_result"
    __table_args__ = {'schema': 'qa_test'}
    
    result_id = Column(Integer, primary_key=True, autoincrement=True)
    object_id = Column(Integer, nullable=False)
    object_type = Column(String, nullable=False)  # e.g., 'step', 'sub_step', 'script'
    reason = Column(String, nullable=False)
    result = Column(Boolean, nullable=False)  # e.g., passed/failed
