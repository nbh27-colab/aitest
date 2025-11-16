from fastapi import APIRouter, HTTPException, UploadFile, File

# from src.api.helper.db_session import getdb, get_async_db
from src.services.casefile.casefile import CaseFileService


router = APIRouter()

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
    
