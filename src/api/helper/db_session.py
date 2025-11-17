from src.data.database.db_engine import SessionLocal, AsyncSessionLocal

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_async_db():
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()