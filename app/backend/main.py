"""FastAPI entrypoint.

CORS + rate limiter are wired in day one per the plan. Auth dependency is
inactive until AUTH_REQUIRED=true.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routes import (
    alerts,
    areas,
    briefings,
    gauges,
    health,
    indicators,
    reports,
    stations,
)
from main_limiter import limiter
from settings import settings

app = FastAPI(
    title="OysterHealth",
    version="0.1.0",
)

# --- Rate limiter ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
# Wired before any auth-protected route exists so JWT preflight works the
# moment AUTH_REQUIRED flips to true.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# --- Routers ---
app.include_router(health.router)
app.include_router(areas.router)
app.include_router(gauges.router)
app.include_router(stations.router)
app.include_router(indicators.router)
app.include_router(alerts.router)
app.include_router(briefings.router)
app.include_router(reports.router)
