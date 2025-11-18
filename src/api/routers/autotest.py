"""
Router for autotest service
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
import os

from src.api.helper.db_session import get_db
from src.services.autotest.workflow import AutoTestWorkflow
from src.data.minIO.minIO_manager import PrivateS3
from config.settings import MinIOSettings


router = APIRouter(prefix="/autotest", tags=["autotest"])


class AutoTestRequest(BaseModel):
    """Request body for autotest"""
    test_case_id: int
    login_info_id: int
    openai_api_key: Optional[str] = None  # Optional, sẽ dùng env var nếu không có


class AutoTestResponse(BaseModel):
    """Response for autotest"""
    status: str
    message: str
    test_case_id: int
    result: Optional[dict] = None


@router.post("/run", response_model=AutoTestResponse)
async def run_autotest(
    request: AutoTestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Trigger autotest workflow cho một test case
    
    Args:
        test_case_id: ID của test case cần autotest
        login_info_id: ID của login info để login
        openai_api_key: OpenAI API key (optional, sẽ dùng từ env nếu không có)
    
    Returns:
        Result của autotest execution
    """
    try:
        # Get OpenAI API key
        api_key = request.openai_api_key or os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="OpenAI API key not provided and not found in environment"
            )
        
        # Initialize MinIO client
        minio_settings = MinIOSettings()
        minio_client = PrivateS3(
            private_url=minio_settings.MINIO_PRIVATE_URL,
            public_url=minio_settings.MINIO_PUBLIC_URL,
            region=minio_settings.MINIO_REGION,
            user=minio_settings.MINIO_USER,
            password=minio_settings.MINIO_PASSWORD
        )
        
        # Create workflow
        workflow = AutoTestWorkflow(
            db_session=db,
            minio_client=minio_client,
            openai_api_key=api_key
        )
        
        # Run workflow
        result = await workflow.run(
            test_case_id=request.test_case_id,
            login_info_id=request.login_info_id
        )
        
        return AutoTestResponse(
            status="success",
            message=f"Autotest completed with status: {result['status']}",
            test_case_id=request.test_case_id,
            result=result
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Autotest failed: {str(e)}"
        )


@router.get("/status/{test_case_id}")
async def get_autotest_status(
    test_case_id: int,
    db: Session = Depends(get_db)
):
    """
    Lấy status của autotest cho một test case
    (Dựa vào test_result table)
    """
    from src.models.test_result import TestResult
    from sqlalchemy import desc
    
    # Get recent test results for this test case
    results = db.query(TestResult)\
        .filter(TestResult.object_type == 'sub_step')\
        .order_by(desc(TestResult.created_at))\
        .limit(50)\
        .all()
    
    # TODO: Filter by test_case_id (need to join with sub_step and step)
    
    return {
        "test_case_id": test_case_id,
        "total_results": len(results),
        "results": [
            {
                "object_id": r.object_id,
                "result": r.result,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in results
        ]
    }
