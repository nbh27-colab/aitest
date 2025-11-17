from sqlalchemy.future import select

from src.data.database.crud.dbs_manager import AsyncDatabaseManager
from src.models import CaseSheet


class CaseSheetService:
    @staticmethod
    async def get_casesheet_ids_for_casefile_async(case_file_id: int):
        db_manager = AsyncDatabaseManager()
        async with db_manager.connect_session_async() as db:
            stmt = (
                select(CaseSheet.case_sheet_id)
                .where(CaseSheet.case_file_id == case_file_id)
                .order_by(CaseSheet.case_sheet_id.asc())
            )
            result = await db.execute(stmt)
            return result.scalars().all()
        
    @staticmethod
    async def get_casesheet_by_id(case_sheet_id: int):
        db_manager = AsyncDatabaseManager()
        async with db_manager.connect_session_async() as db:
            stmt = select(CaseSheet).where(CaseSheet.case_sheet_id == case_sheet_id)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()