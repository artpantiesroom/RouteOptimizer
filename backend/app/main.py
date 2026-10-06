from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import geocode, health, matrix, parse
from .core.config import get_settings

settings = get_settings()

app = FastAPI(title="RouteOptimizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(parse.router, prefix="/api")
app.include_router(geocode.router, prefix="/api")
app.include_router(matrix.router, prefix="/api")
