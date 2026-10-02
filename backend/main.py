from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth.auth import router as auth_router
from websocket import router as ws_router
from database.database import engine, Base

# Create database tables upon startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="TravelAgent Backend")

# Configure CORS specifically for local development
origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:5500",  # Common VSCode Live Server port
    "http://127.0.0.1:8000",
    "*"  # Fallback for file:// based local viewing during early development
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Authentication Routes
app.include_router(auth_router)

# Include WebSocket Routes
app.include_router(ws_router)

@app.get("/health")
async def health_check():
    """
    Simple health check endpoint to verify backend is running.
    """
    return {
        "status": "ok",
        "message": "TravelAgent backend is running"
    }
