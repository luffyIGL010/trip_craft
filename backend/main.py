import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from auth.auth import require_jwt_secret_key, router as auth_router
from websocket import router as ws_router
from preferences.router import router as preferences_router
from database.database import engine, Base, ensure_travel_preference_budget_basis

load_dotenv()

# Fail startup rather than signing tokens with an unsafe fallback.
require_jwt_secret_key()

# Create database tables upon startup
Base.metadata.create_all(bind=engine)
ensure_travel_preference_budget_basis()

app = FastAPI(title="TravelAgent Backend")

# Allow local development origins and explicitly configured deployed frontends.
origins = {
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:5500",  # Common VSCode Live Server port
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://localhost:5500",
    "https://tripcraft-frontend-psi.vercel.app",
}
origins.update(
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS", "https://tripcraft-frontend-psi.vercel.app"
    ).split(",")
    if origin.strip()
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Authentication Routes
app.include_router(auth_router)
app.include_router(preferences_router)

# Include WebSocket Routes
app.include_router(ws_router)

from payments.router import router as payments_router
app.include_router(payments_router)

@app.get("/health")
async def health_check():
    """
    Simple health check endpoint to verify backend is running.
    """
    return {
        "status": "ok",
        "message": "TravelAgent backend is running"
    }
