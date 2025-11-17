from sqlalchemy import Column, Integer, Text

from .base import Base
from .share_attribute import ShareAttribute


class SubStep(Base, ShareAttribute):
    __tablename__ = "sub_step"
    __table_args__ = {"schema": "qa_test"}

    sub_step_id = Column(Integer, primary_key=True, autoincrement=True)
    step_id = Column(Integer, nullable=False)
    sub_step_order = Column(Integer, nullable=False)
    sub_step_content = Column(Text)
    expected_result = Column(Text)
