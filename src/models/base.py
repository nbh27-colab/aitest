from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

metadata = MetaData(schema="qa_test")
Base = declarative_base(metadata=metadata)