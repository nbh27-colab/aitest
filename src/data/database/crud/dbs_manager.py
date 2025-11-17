# src/data/database/db_manager.py
from contextlib import asynccontextmanager
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import select, text, create_engine
from src.models import CaseSheet, SubStep, GeneratedScript
from src.data.database.db_engine import AsyncSessionLocal, engine, async_engine

class AsyncDatabaseManager:
    def __init__(self):
        self.engine = async_engine
        self.SessionLocal = AsyncSessionLocal

    @asynccontextmanager
    async def connect_session_async(self):
        async with self.SessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()
            
class DatabaseManager:
    def __init__(self):
        self.engine = engine
        self.SessionLocal = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )
    

    def connect_session(self) -> Session:
        """Return a new SQLAlchemy session."""
        return self.SessionLocal()
    
    def get_session(self):
        """Alias for connect_session() for backward compatibility."""
        return self.connect_session()

    # ----------------- Database Utilities -----------------
    def clear_database(self):
        """Drop and recreate public schema (keeps database)."""
        try:
            with self.engine.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text("DROP SCHEMA public CASCADE;"))
                conn.execute(text("CREATE SCHEMA public;"))
            print(
                f"- All contents in database '{self.engine.url.database}' cleared (DB kept)."
            )
        except Exception as e:
            print("- Error clearing database:", e)

    def drop_database(self):
        """Drop the entire database (requires superuser)."""
        dbname = self.engine.url.database
        try:
            url = self.engine.url.set(database="postgres")
            engine_postgres = create_engine(url)
            with engine_postgres.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                # terminate active connections
                conn.execute(
                    text(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = :dbname
                        AND pid <> pg_backend_pid();
                    """
                    ),
                    {"dbname": dbname},
                )
                # drop database if exists
                conn.execute(text(f"DROP DATABASE IF EXISTS {dbname}"))
                print(f"- Database '{dbname}' dropped.")
        except Exception as e:
            print("- Error dropping database:", e)

    # ----------------- Data Access Methods -----------------
    def get_substeps_for_step(self, step_id: int):
        """Return ordered substeps for a step."""
        with self.connect_session() as db:
            return (
                db.query(SubStep)
                .filter(SubStep.step_id == step_id)
                .order_by(SubStep.sub_step_order.asc())
                .all()
            )

    def get_casesheet_ids_for_casefile(self, case_file_id: int):
        """Return all case sheet IDs in a case file as a flat list."""
        with self.connect_session() as db:
            stmt = (
                select(CaseSheet.case_sheet_id)
                .where(CaseSheet.case_file_id == case_file_id)
                .order_by(CaseSheet.case_sheet_id.asc())
            )
            return db.execute(stmt).scalars().all()

    def get_script_map_for_step(self, step_id: int):
        """Return mapping sub_step_id -> GeneratedScript for a step."""
        with self.connect_session() as db:
            substeps = db.query(SubStep).filter(SubStep.step_id == step_id).all()
            substep_ids = [s.sub_step_id for s in substeps]
            scripts = (
                db.query(GeneratedScript)
                .filter(GeneratedScript.sub_step_id.in_(substep_ids))
                .all()
            )
            return {script.sub_step_id: script for script in scripts}


if __name__ == "__main__":
    db = DatabaseManager()
    # Example usage
    db.clear_database()
