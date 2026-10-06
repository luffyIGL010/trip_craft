# TravelAgent

TravelAgent is an upcoming Agentic AI travel platform designed to assist users in planning, booking, and managing their travel itineraries through a seamless chat interface and specialized AI agents.

## Project Phases

### Phase 1: Foundation
- Basic HTML/CSS/JS frontend
- FastAPI backend with a `/health` endpoint
- CORS setup and HTTP communication

### Phase 2: User Authentication
- SQLite & SQLAlchemy database setup
- JWT-based authentication (Register, Login, Protected endpoints)
- Password hashing with Bcrypt

### Phase 3: WebSocket Foundation
- FastAPI WebSocket implementation (`/ws` endpoint)
- Secure WebSocket connections using JWT for authentication

### Phase 4: Master Agent / Orchestrator Foundation (Current)
- **Groq LLM** integration (Llama 3.3 70B) to parse natural-language travel requests
- Master Agent Orchestrator: understands inputs, identifies required future tasks, and detects missing info
- Structured JSON output enforced via Pydantic schemas
- Clean fallback **mock mode** if no API key is configured
- Frontend displays the structured plan in a clean, readable format

None of the specific travel action agents (flights, hotels, maps) are implemented yet.

## Project Structure

```text
.
├── frontend/
│   ├── index.html       # Main dashboard (Protected) with travel chat UI
│   ├── login.html       # Login page
│   ├── register.html    # Registration page
│   ├── style.css        # Stylesheet
│   ├── script.js        # Dashboard HTTP logic (health check, auth state)
│   ├── auth.js          # Authentication and JWT token management
│   └── ws.js            # WebSocket client & Master Agent plan renderer
│
├── backend/
│   ├── main.py          # FastAPI application, CORS, route registration
│   ├── requirements.txt # Python dependencies
│   ├── websocket.py     # WebSocket endpoint → Master Agent pipeline
│   ├── database/
│   │   ├── database.py  # SQLite setup and SQLAlchemy engine
│   │   └── models.py    # User database model
│   ├── auth/
│   │   ├── auth.py      # Auth endpoints (register, login, me, logout)
│   │   └── schemas.py   # Pydantic schemas for auth
│   └── agents/
│       ├── __init__.py
│       ├── master_agent.py  # Groq LLM integration, orchestrator logic
│       └── schemas.py       # Pydantic models for structured plan output
│
├── .env.example         # Template for environment variables
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## How to Run the Project

### 1. Backend Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Your `backend/.env` file should contain:
```
JWT_SECRET_KEY=<your-jwt-secret>
GROQ_API_KEY=<your-groq-api-key>
```

- **JWT_SECRET_KEY**: Generate with `openssl rand -hex 32`
- **GROQ_API_KEY**: Get from [console.groq.com](https://console.groq.com). If not provided, the Master Agent runs in mock mode.

### 3. Start the Server

```bash
# Inside backend/ with venv activated
uvicorn main:app --reload
```

### 4. Open the Frontend

Open `frontend/index.html` in your browser.

### 5. Test Phase 4

1. Log in (or register first if needed)
2. Click **Connect** to establish the WebSocket
3. Try these test messages:

| Test | Input | Expected Result |
|------|-------|-----------------|
| 1 | `Plan a 5 day trip to Goa from Delhi for 2 people with a budget of 50000` | Destination: Goa, Origin: Delhi, Duration: 5, Travelers: 2, Budget: 50000, Tasks listed |
| 2 | `I want to visit Paris` | Destination: Paris, Missing: origin, dates, travelers, budget |
| 3 | `Plan a trip to Manali for 3 people from Delhi` | Destination: Manali, Origin: Delhi, Travelers: 3, Missing: dates, budget |
| 4 | *(empty message)* | Error: "Please enter a travel request" |

## What is NOT Built Yet (Intentionally)

- Specialized agents (Travel, Hotel, Budget, Activity, Weather, Itinerary)
- Real API calls for flights, hotels, weather, maps
- Long-term AI memory (Redis, Vector DBs)
- Production deployment
