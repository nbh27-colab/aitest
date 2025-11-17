from sqlalchemy import Column, Integer, String, Boolean
from .base import Base


class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(50), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    verification_code = Column(String(100), nullable=True)
    is_verified = Column(Boolean, default=False)
