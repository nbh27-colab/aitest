import os

from config.settings import MinIOSettings
from utils.s3_client import get_s3

class UploadExtractor:

    def __init__(self, filepath=None, fill_value="", forward_fill_columns=None, output_folder=None, use_minio=False):
        self.filepath = filepath
        self.fill_value = fill_value
        self.forward_fill_columns = forward_fill_columns or []
        self.output_folder = output_folder
        self.use_minio = use_minio

        self._minio_setting = None
        self._s3 = None

        self.loader = None
        self.cleaner = None
        self.extractor = None

    @property
    def minio_setting(self):
        if self._minio_setting is None:
            self._minio_setting = MinIOSettings()
        return self._minio_setting

    @property
    def s3(self):
        if not self.use_minio:
            raise ValueError("MinIO is not enabled. Initialize with use_minio=True")
        return get_s3()

    def upload_to_minio(self, local_file_path):
        if not self.use_minio:
            raise ValueError("MinIO is not enabled. Initialize with use_minio=True")
        
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"The file {local_file_path} does not exist.")
        
        print(f"[INFO] Uploading file to MinIO: {local_file_path}")

        public_url, remote_file_path = self.s3.upload_file_from_path(
            bucket_name=self.minio_setting.BUCKET_NAME,
            local_file_path=local_file_path,
            remote_folder=self.minio_setting.FOLDER_NAME
        )

        print(f"[INFO] File uploaded to MinIO at: {public_url}")
        return public_url, remote_file_path