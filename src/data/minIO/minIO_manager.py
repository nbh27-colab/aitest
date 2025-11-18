import boto3
from typing import Union, Tuple
from pathlib import Path
from botocore.exceptions import ClientError
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path
import tempfile

from src.core.process_file_name import FileNameProcessor

class PrivateS3:
    def __init__(self, private_url: str, public_url: str, region: str, user: str, password: str) -> None:
        self.public_url = public_url.rstrip('/')

        self.s3_resource = boto3.resource(
            service_name='s3',
            region_name=region,
            endpoint_url=private_url,
            aws_access_key_id=user,
            aws_secret_access_key=password,
            verify=False
        )

    def ensure_bucket_exists(self, bucket_name: str) -> None:
        s3_client = self.s3_resource.meta.client
        try:
            s3_client.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            error_code = int(e.response['Error']['Code'])
            if error_code == 404:
                print(f"[INFO] Bucket '{bucket_name}' does not exist. Creating new bucket.")
                s3_client.create_bucket(Bucket=bucket_name)
                # Set bucket policy to allow public read access
                self.set_bucket_public_read_policy(bucket_name)
            else:
                raise e
    
    def set_bucket_public_read_policy(self, bucket_name: str) -> None:
        """Set bucket policy to allow public read access"""
        import json
        
        s3_client = self.s3_resource.meta.client
        bucket_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                    "Resource": f"arn:aws:s3:::{bucket_name}"
                },
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/*"
                }
            ]
        }
        
        try:
            s3_client.put_bucket_policy(
                Bucket=bucket_name,
                Policy=json.dumps(bucket_policy)
            )
            print(f"[INFO] Set public read policy for bucket '{bucket_name}'")
        except ClientError as e:
            print(f"[WARNING] Could not set bucket policy: {e}")

    def upload_file(self, bucket_name: str, data: bytes,remote_file_path: str) -> None:
        self.ensure_bucket_exists(bucket_name)
        print(f"[INFO] Uploading file to bucket '{bucket_name}', path: '{remote_file_path}'")
        self.s3_resource.Object(bucket_name, remote_file_path).put(Body=data)
        print(f"[INFO] File uploaded successfully to '{bucket_name}/{remote_file_path}'")

    def get_file_public_url(self, bucket_name: str, remote_file_path: str) -> str:
        return f"{self.public_url}/{bucket_name}/{remote_file_path}"

    def upload_file_from_path(self, bucket_name: str, local_file_path: Union[str, Path], remote_folder: str) -> Tuple[str, str]:

        local_path = Path(local_file_path)
        if not local_path.exists():
            raise FileNotFoundError(f"The file {local_file_path} does not exist.")
        
        with open(local_path, 'rb') as f:
            data = f.read()

        process_file_name = FileNameProcessor(str(local_file_path))

        safe_filename = process_file_name.get_safe_filename_with_extension()
        remote_file_path = f"{remote_folder}/{safe_filename}"

        print(f"[DEBUG] Uploading as: {remote_file_path}")
        self.upload_file(bucket_name, data, remote_file_path)

        public_url = self.get_file_public_url(bucket_name, remote_file_path)
        return public_url, remote_file_path
    
    @contextmanager
    def download_file(
        self,
        bucket_name: str,
        remote_file_path: str,
    ) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file_path = Path(temp_dir) / Path(remote_file_path).name

            self.s3_resource.Bucket(bucket_name).download_file(
                Key=remote_file_path,
                Filename=str(local_file_path)
            )

            yield local_file_path