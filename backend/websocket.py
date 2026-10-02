import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from database.database import get_db
from database.models import User
from auth.auth import SECRET_KEY, ALGORITHM

router = APIRouter(tags=["websocket"])

def get_user_from_token(token: str, db: Session):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            return None
        return db.query(User).filter(User.email == email).first()
    except JWTError:
        return None

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    token: str = Query(None), 
    db: Session = Depends(get_db)
):
    # Accept the connection first so we can send clear error messages instead of an HTTP 403 (1006 in browser)
    await websocket.accept()
    
    if not token:
        await websocket.send_json({"error": "Missing authentication token"})
        await websocket.close(code=1008)
        return
        
    user = get_user_from_token(token, db)
    if not user:
        await websocket.send_json({"error": "Invalid or expired authentication token"})
        await websocket.close(code=1008)
        return
    
    try:
        while True:
            text_data = await websocket.receive_text()
            
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format. Expected JSON."})
                continue
                
            # Process the expected message type
            if data.get("type") == "message":
                response = {
                    "type": "message",
                    "message": "WebSocket connection successful",
                    "user_id": user.id
                }
                await websocket.send_json(response)
            else:
                await websocket.send_json({"error": "Unknown message type."})
                
    except WebSocketDisconnect:
        # Client disconnected normally
        pass
    except Exception as e:
        # Prevent server crash on unexpected error
        try:
            await websocket.close(code=1011)
        except:
            pass
