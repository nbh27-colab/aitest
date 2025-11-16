from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routers import (
    upload,
)

# instance
app = FastAPI(
    title="My API",
    description="This is a sample API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Welcome to My API!"}