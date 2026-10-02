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

### Phase 3: WebSocket Foundation (Current)
- FastAPI WebSocket implementation (`/ws` endpoint)
- Secure WebSocket connections using the Phase 2 JWT for authentication
- Real-time bidirectional message exchange handling JSON format
- Real-time chat UI on the frontend

None of the advanced AI features, agents, or external APIs are implemented yet.

## Project Structure

```text
.
├── frontend/
│   ├── index.html     # Main dashboard (Protected) with WebSocket UI
│   ├── login.html     # Login page
│   ├── register.html  # Registration page
│   ├── style.css      # Stylesheet
│   ├── script.js      # Dashboard HTTP logic
│   ├── auth.js        # Authentication and JWT token management
│   └── ws.js          # WebSocket client logic
│
├── backend/
│   ├── main.py        # The FastAPI application & CORS
│   ├── requirements.txt # Python dependencies
│   ├── websocket.py   # WebSocket endpoint & JWT validation
│   ├── database/
│   │   ├── database.py # SQLite setup and SQLAlchemy engine
│   │   └── models.py   # User database model
│   └── auth/
│       ├── auth.py     # Auth endpoints
│       └── schemas.py  # Pydantic schemas
│
├── .env.example       # Example environment variables
├── .gitignore         # Git ignore rules
└── README.md          # Project documentation
```

## How to Run the Project

### 1. Backend Setup

First, set up the Python backend.

**Create and activate a virtual environment:**
```bash
cd backend
python3 -m venv venv
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate
```

**Install requirements:**
```bash
pip install -r requirements.txt
```

**Set up environment variables:**
In the root directory, create a `.env` file containing your `JWT_SECRET_KEY` if you haven't already.

**Start the FastAPI server:**
```bash
uvicorn main:app --reload
```
The backend will start on `http://127.0.0.1:8000`.

### 2. Frontend Setup

Open `frontend/index.html` in your web browser. 

### 3. How to Test Phase 3 (WebSockets)

1. **Login**: Make sure you are registered and logged into the application. You will be on the main Dashboard.
2. **Connect**: Scroll down to the "Real-time Connection (WebSocket)" section and click **Connect**.
3. **Verify Connection**: The status should change to a green **Connected** badge, and the message window will indicate "Connected to WebSocket server."
4. **Send a Message**: In the input box, type a message like `Hello TravelAgent` (or a raw JSON string like `{"type":"message","message":"Hello"}`) and click **Send**.
5. **Verify Response**: You should see your message echo in the chat window, immediately followed by the server's JSON response (e.g., `{"type": "message", "message": "WebSocket connection successful", "user_id": 1}`).
6. **Disconnect**: Click the **Disconnect** button. The status should revert to **Disconnected**, and you will no longer be able to send messages.
7. **Security Check**: The backend verifies your JWT before the WebSocket upgrade completes. If your token is invalid, the connection is instantly closed with code 1008.

## What is NOT Built Yet (Intentionally)

This is Phase 3. The following features belong to future phases:
- AI agents and LLM integration (Master Agent, Travel Agent, etc.)
- Long-term AI memory (Redis, Vector DBs, etc.)
- External travel APIs (flights, hotels, weather, etc.)
