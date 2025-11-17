import asyncio
from fastapi import APIRouter, Query, HTTPException
from typing import List

from src.services.casesheet.casesheet import CaseSheetService

router = APIRouter()

@router.post("/generate-substep/")
async def gen_substep(
    casesheet_id: int = Query(None),
    case_file_id: int = Query(None),
):
    
    try:
        if not casesheet_id and not case_file_id:
            raise HTTPException(
                status_code=400,
                detail="Either casesheet_id or case_file_id must be provided.",
            )
        
        if case_file_id is not None:
            casesheet_ids = await CaseSheetService.get_casesheet_ids_for_casefile_async(
                case_file_id
            )
            if not casesheet_ids:
                print(f"No casesheets found for case_file_id: {case_file_id}")
                return {
                    "status": "success",
                    "substeps": {},
                    "message": f"No casesheets found for case_file_id: {case_file_id}",
                }
        else:
            casesheet = await CaseSheetService.get_casesheet_by_id(casesheet_id)
            case_file_id = casesheet.case_file_id
            casesheet_ids = [casesheet_id]

        task_id = f"substep_{case_file_id or casesheet_id}_{int(asyncio.get_event_loop().time() * 1000)}"
        
        _generate_substeps_impl(casesheet_ids, task_id)

        return {
            "status": "success",
            "task_id": task_id,
            "casesheet_ids": casesheet_ids,
            "message": "Substep generation task initiated.",
        }
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Substep generation failed: {str(e)}")
    
def _generate_substeps_impl(casesheet_ids: List[int], task_id: str):
    try:
        for case_sheet_id in casesheet_ids:
            await gen