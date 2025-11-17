from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from fastapi import Depends
from sqlalchemy.orm import Session

# from src.api.helper.db_session import getdb, get_async_db
from src.api.helper.db_session import get_async_db
from src.services.casefile.casefile import CaseFileService
from src.services.upload_pipeline.upload import UploadService


router = APIRouter(
    tags=["File Upload"],
)

@router.post("/upload-file")
async def upload_file(file: UploadFile = File(...)):
    allowed_extensions = [".xlsx", ".csv"]
    if not any(file.filename.endswith(ext) for ext in allowed_extensions):
        raise HTTPException(status_code=400, detail="Invalid file type. Only .xlsx and .csv are allowed.")
    
    try:
        result = await CaseFileService.upload_file(file)
        return {"status": "success", "message": "file Uploaded successfully", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
    
@router.post("/upload")
async def upload(
    filepath: str = Body(...),
    project_id: int = Body(...),
    login_info_id: int = Body(None),
    db: Session = Depends(get_async_db),
):
    result = await UploadService.insert_test_cases(filepath, project_id)
    case_file_id = result
    if case_file_id and login_info_id:
        case_file = await CaseFileService.get_case_file_by_id(case_file_id, db)
        if case_file:
            case_file.login_info_id = login_info_id
            await db.commit()
            await db.refresh(case_file)