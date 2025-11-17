from sqlalchemy import Column, Integer, Text

from .base import Base
from .share_attribute import ShareAttribute

class Step(Base, ShareAttribute):
    __tablename__ = "step"
    __table_args__ = {'schema': 'qa_test'}
    
    step_id = Column(Integer, primary_key=True, autoincrement=True)
    test_case_id = Column(Integer, nullable=False)
    project_id = Column(Integer, nullable=True)
    step_order = Column(Integer)
    action = Column(Text)
    expected_result = Column(Text)
    comment = Column(Text)