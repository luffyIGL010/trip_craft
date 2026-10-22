import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from jose import JWTError

from database.database import get_db
from database.models import User
from auth.auth import decode_access_token
from agents.master_agent import MasterAgent
from preferences.service import (
    extract_explicit_preferences,
    list_preferences,
    upsert_preference,
)

# Setup logging for development
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])
AUTH_SUBPROTOCOL_PREFIX = "travelagent.auth."

# Initialize the Master Agent orchestrator once at startup
master_agent = MasterAgent()

def get_user_from_token(token: str, db: Session):
    """Decode JWT and return the User object, or None if invalid."""
    try:
        payload = decode_access_token(token)
        email: str = payload.get("sub")
        if not email:
            return None
        return db.query(User).filter(User.email == email).first()
    except JWTError:
        return None

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    db: Session = Depends(get_db)
):
    offered_protocols = websocket.scope.get("subprotocols", [])
    if "travelagent" not in offered_protocols:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol="travelagent")
    
    # --- JWT Authentication ---
    token_protocols = [
        protocol[len(AUTH_SUBPROTOCOL_PREFIX):]
        for protocol in offered_protocols
        if protocol.startswith(AUTH_SUBPROTOCOL_PREFIX)
    ]
    token = token_protocols[0] if len(token_protocols) == 1 else None
    if not token:
        await websocket.send_json({"error": "Missing authentication token"})
        await websocket.close(code=1008)
        return
        
    user = get_user_from_token(token, db)
    if not user:
        await websocket.send_json({"error": "Invalid or expired authentication token"})
        await websocket.close(code=1008)
        return
        
    logger.info(f"WebSocket connected for user ID: {user.id} ({user.email})")
    
    # --- Message Loop ---
    try:
        while True:
            text_data = await websocket.receive_text()
            
            # Parse JSON
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON format."})
                continue
                
            if data.get("type") == "message":
                user_message = data.get("message", "").strip()
                
                # Validate non-empty
                if not user_message:
                    await websocket.send_json({"error": "Please enter a travel request."})
                    continue
                
                logger.info("User %s submitted a travel request.", user.id)
                
                try:
                    # Provide an async callback for status updates
                    async def send_status(agent_name, status):
                        await websocket.send_json({
                            "type": "agent_status",
                            "agent": agent_name,
                            "status": status
                        })
                    
                    try:
                        for preference in extract_explicit_preferences(user_message):
                            upsert_preference(
                                db,
                                user.id,
                                **preference,
                                replace_single_value=False,
                            )
                        db.commit()
                        saved_preferences = [
                            {
                                "category": preference.category,
                                "value": preference.value,
                                "min_amount": preference.min_amount,
                                "max_amount": preference.max_amount,
                                "currency": preference.currency,
                                "budget_basis": preference.budget_basis,
                            }
                            for preference in list_preferences(db, user.id)
                        ]
                    except SQLAlchemyError:
                        db.rollback()
                        logger.warning(
                            "Travel preference memory is unavailable for this request."
                        )
                        saved_preferences = []

                    # Call the Master Agent with only this user's saved travel preferences.
                    result_data = await master_agent.process_request(
                        user_message,
                        status_callback=send_status,
                        saved_preferences=saved_preferences,
                    )
                    
                    # Return the structured plan and transport results
                    await websocket.send_json({
                        "type": "plan",
                        "data": result_data
                    })
                    logger.info(f"Plan and results sent to user {user.id}")
                    
                except ValueError as ve:
                    await websocket.send_json({"error": str(ve)})
                except Exception as e:
                    logger.error(f"Master Agent error for user {user.id}: {e}")
                    await websocket.send_json({"error": "Failed to process your request. Please try again."})
            else:
                await websocket.send_json({"error": "Unknown message type."})
                
    except WebSocketDisconnect:
        logger.info(f"User {user.id} disconnected.")
    except Exception as e:
        logger.error(f"Unexpected WebSocket error for user {user.id}: {e}")
        try:
            await websocket.close(code=1011)
        except:
            pass
