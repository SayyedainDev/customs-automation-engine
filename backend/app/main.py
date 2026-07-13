from fastapi import FastAPI
from app.api.routes import documents
from app.api.routes import health

app=FastAPI(
    title="Enterprise Customs Engine",
    description="Enterprise Customs Engine is a powerful and flexible platform for managing customs operations and compliance. It provides a comprehensive suite of tools and features to streamline customs processes, ensure regulatory compliance, and optimize supply chain efficiency.",
    version="1.0.0",
)

app.include_router(health.router)

app.include_router(
    documents.router,
)

@app.get("/")

def root()->dict[str,str]:
    return{
        "message": "Welcome to the Enterprise Customs Engine API!",
        "status": "API is running "
    }