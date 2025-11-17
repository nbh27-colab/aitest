from sqlalchemy import Column, Integer, String, Text

from .base import Base
from .share_attribute import ShareAttribute

class LoginInfo(Base, ShareAttribute):
    __tablename__ = "login_info"
    login_info_id = Column(Integer, primary_key=True, autoincrement=True)
    created_by_id = Column(Integer, nullable=True)
    email = Column(String(255), nullable=False)
    password = Column(String(255), nullable=False)
    web_url = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
