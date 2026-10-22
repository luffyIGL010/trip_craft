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
- The backend refuses to start if `JWT_SECRET_KEY` is missing. WebSocket authentication sends the JWT in the handshake subprotocol, not the URL.
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

## Phase 7: Itinerary Agent

Requests for an itinerary, day-by-day plan, or daily schedule are routed through the existing authenticated chat workflow. With `GROQ_API_KEY` configured, the Itinerary Agent uses the existing Groq client to produce one day per trip date, with practical activity and meal/free-time suggestions. It receives only the available flight timing and hotel option details; mock flight results are not treated as travel-time constraints. The UI labels all itinerary content as AI-generated suggestions and asks travelers to verify hours, availability, prices, and local conditions. No destination/activity API is currently integrated, so live opening hours, ticket prices, availability, and travel durations are not provided. Itineraries are limited to 30 calendar days; without model configuration or on malformed model output, the agent returns a clear error instead of fabricated suggestions.

## What is NOT Built Yet (Intentionally)

- Specialized activity, budget, and weather agents
- Real API calls for weather, maps, and activities
- Conversation-history memory (Redis or a vector database)
- Production deployment

## Persistent Travel Preferences

The dashboard includes a per-user travel preference profile for destinations, budget ranges, accommodation style, interests, transportation, trip pace, dietary preferences, and accessibility requirements. Budget ranges explicitly select **total trip (all travelers combined)** or **per person**. Existing budget records are migrated as **meaning not set** and are not compared until the user edits them and selects a basis. Users can view, add, edit, delete, or clear their profile from the **Travel Preferences** section.

Preferences are stored in the existing SQLite database in a `travel_preferences` table. The existing `Base.metadata.create_all()` startup step creates this new table without deleting or recreating existing tables. Startup also adds the `budget_basis` column in place for an existing preference table. This additive SQLite change leaves existing budget amounts untouched and marks their basis unspecified. Start the backend from `backend/` as usual so it continues using `backend/travelagent.db`.

The authenticated API is under `/api/preferences`:

- `GET /api/preferences` lists the signed-in user's preferences.
- `POST /api/preferences` creates or updates a preference.
- `PUT /api/preferences/{preference_id}` updates an owned preference.
- `DELETE /api/preferences/{preference_id}` deletes an owned preference.
- `DELETE /api/preferences` clears the signed-in user's profile.

The chat workflow saves only recognized, explicitly recurring preferences (for example, “I usually prefer budget hotels and historical places”). Saved preferences are suggestions: current-trip instructions take precedence. Total-trip INR budgets are compared with a verified combined estimate; per-traveler ranges are compared with that estimate divided by the requested traveler count. Provisional estimates are not used for saved-budget comparisons. Unspecified budgets are not compared. Flight fares are labeled as round-trip only when the provider option includes both route legs; otherwise the UI identifies an outbound-only or unverified fare and does not include an unverified return. SerpApi's Google Flights response documents a `price` and a search `adults` parameter, but does not establish whether the returned price is per traveler or for the whole party. If numeric flight and hotel totals are available but flight itinerary or traveler pricing scope is unverified, the UI shows their unscaled component sum as a clearly labeled provisional estimate; it does not claim a complete-trip total, infer a return fare, or offer Sandbox checkout for provisional prices. Confirmed traveler-scope prices may be multiplied by the requested traveler count according to the provider-declared scope. For round-trip searches, return choices are fetched separately using the selected outbound `departure_token`; those return details are not assigned a separate fare or treated as interchangeable pairings. Duplicate combinations use provider option IDs when available and normalized option details otherwise. The itinerary agent uses existing interests and preferences as planning context; it does not claim live activity availability or venue facts.

Run focused preference and fare-mapping checks from the repository root with:

```bash
PYTHONPATH=backend venv/bin/python -m unittest discover -s backend/tests -v
```
