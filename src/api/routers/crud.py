from typing import Optional
from fastapi import APIRouter, HTTPException, Body, Query

from src.data.database.crud.table_manager import TableManager

router = APIRouter(
    tags=["CRUD Operations"],
)

def get_table_manager():
    return TableManager()

@router.post("/table/{table_name}/insert")
async def insert_row(
    table_name: str,
    data: dict = Body(...),
):
    try:
        table_manager = get_table_manager()
        table_manager.insert_row("qa_test", table_name, data)
        return{"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/table/{table_name}")
async def get_table_rows(
    table_name: str,
    filters: Optional[dict] = Body(None),
    order_by: Optional[str] = Query(None),
    order_direction: Optional[str] = Query(None),
):
    try:
        table_manager = get_table_manager()
        rows = table_manager.fetch_rows("qa_test", table_name, filters)
        if order_by:
            reverse = order_direction.lower() == "desc"
            rows = sorted(rows, key=lambda x: x.get(order_by, ""), reverse=reverse)
        return {"rows": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/table/{table_name}")
async def update_table_rows(
    table_name: str,
    data: dict = Body(...),
    where: dict = Body(...),
):
    try:
        table_manager = get_table_manager()
        table_manager.update_rows("qa_test", table_name, data, where)
        return {
            "status": "success",
            "message": f"Rows in {table_name} updated successfully.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.delete("/table/{table_name}")
async def delete_table_rows(
    table_name: str,
    where: dict = Body(...),
):
    try:
        table_manager = get_table_manager()
        deleted_count = table_manager.delete_rows("qa_test", table_name, where)
        return {
            "status": "success",
            "deleted_rows": deleted_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))