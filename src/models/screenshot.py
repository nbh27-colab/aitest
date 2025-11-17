from sqlalchemy import Column, Integer, Text

from .base import Base
from .share_attribute import ShareAttribute


class Screenshot(Base,ShareAttribute):
    __tablename__ = "screenshot"
    __table_args__ = {'schema': 'qa_test'}
    
    screenshot_id = Column(Integer, primary_key=True, autoincrement=True)
    generated_script_id = Column(Integer, nullable=False)
    screenshot_link = Column(Text)
