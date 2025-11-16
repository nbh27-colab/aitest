from pydantic_settings import BaseSettings, SettingsConfigDict

class QABaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

class PostgreSQLSettings(QABaseConfig):
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    SCHEMA_NAME: str

class ReadFileSettings:
    dox_example: str = "data/examples/example.docx"
    output_folder: str = "data/output"
    excel_example: str = "data/examples/example.xlsx"
    csv_example: str = "data/examples/example.csv"

class MinIOSettings(QABaseConfig):
    MINIO_PRIVATE_URL: str
    MINIO_PUBLIC_URL: str
    MINIO_REGION: str
    MINIO_USER: str
    MINIO_PASSWORD: str
    BUCKET_NAME: str
    FOLDER_NAME: str
    LOGIN_SCREENSHOT_FOLDER: str