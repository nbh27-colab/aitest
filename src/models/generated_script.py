from sqlalchemy import Column, Integer, Text

from .base import Base
from .share_attribute import ShareAttribute


class GeneratedScript(Base, ShareAttribute):
    __tablename__ = "generated_script"
    __table_args__ = {"schema": "qa_test"}

    generated_script_id = Column(Integer, primary_key=True, autoincrement=True)
    sub_step_id = Column(Integer, unique=True, nullable=False)
    script_content = Column(Text)
