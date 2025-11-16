import os
from sqlalchemy.future import select
from fastapi import UploadFile

from src.models import CaseFile
from config.settings import ReadFileSettings
from src.services.extraction.extract_test_case import UploadExtractor

class CaseFileService:

    @staticmethod
    async def get_case_file_by_id(case_file_id: int, db):

        result = await db.execute(
            select(CaseFile).where(CaseFile.case_file_id == case_file_id)
        )
        case_file = result.scalar_one_or_none()
        return case_file
    
    @staticmethod
    async def upload_file(file: UploadFile):
        upload_folder = "data/tmp_uploads/"
        os.makedirs(upload_folder, exist_ok=True)
        temp_path = os.path.join(upload_folder, file.filename)

        # save uploaded file to temp path
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # initialize setting and extractor
        read_settings = ReadFileSettings()
        extractor_minio = UploadExtractor(
            fill_value="",
            output_folder=read_settings.output_folder,
            use_minio=True
        )

        # Upload file to MinIO and return metadata
        public_url, remote_path = extractor_minio.upload_to_minio(temp_path)
        return {
            "file_name": file.filename,
            "file_size": public_url,
            "remote_path": remote_path,
        }
