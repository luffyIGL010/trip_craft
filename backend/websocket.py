import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from jose import JWTError

from database.database import get_db
from database.models import TripPlan, TripPlanChange, User
from auth.auth import decode_access_token
from agents.master_agent import MasterAgent
from services.replanning import (
    ReplanAttemptLimitReached,
    ReplanningService,
    max_replan_attempts,
    request_key,
)
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
                
            if data.get("type") == "replan_decision":
                try:
                    proposal_id = int(data.get("proposal_id"))
                    decision = data.get("decision")
                    if decision not in {"approve", "reject"}:
                        raise ValueError("Decision must be approve or reject.")
                    result = ReplanningService.decide(
                        db,
                        user.id,
                        proposal_id,
                        "approved" if decision == "approve" else "rejected",
                    )
                    await websocket.send_json({
                        "type": "replan_decision",
                        "data": result,
                    })
                except (ValueError, LookupError) as exc:
                    await websocket.send_json({"error": str(exc)})
            elif data.get("type") == "message":
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

                    trip = None
                    is_replan = ReplanningService.is_replan_request(user_message)
                    if is_replan:
                        try:
                            trip_id = int(data.get("trip_id"))
                        except (TypeError, ValueError):
                            trip_id = None
                        if trip_id is None:
                            await websocket.send_json({
                                "error": "Re-planning requires an existing saved trip. Create a trip plan first."
                            })
                            continue
                        trip = db.query(TripPlan).filter(
                            TripPlan.id == trip_id,
                            TripPlan.user_id == user.id,
                        ).first()
                        if trip is None:
                            await websocket.send_json({"error": "Trip plan not found."})
                            continue

                        key = request_key(user.id, trip.id, user_message)
                        previous_proposal = db.query(TripPlanChange).filter(
                            TripPlanChange.trip_id == trip.id,
                            TripPlanChange.user_id == user.id,
                            TripPlanChange.request_key == key,
                        ).first()
                        if previous_proposal:
                            message_type = (
                                "plan"
                                if previous_proposal.status in {
                                    "pending",
                                    "unresolved",
                                    "no_change",
                                }
                                else "replanning"
                            )
                            await websocket.send_json({
                                "type": message_type,
                                "data": json.loads(previous_proposal.response_json),
                            })
                            continue
                        if trip.replan_attempts >= max_replan_attempts():
                            await websocket.send_json({
                                "type": "replanning",
                                "data": {
                                    "trip_id": trip.id,
                                    "status": "attempt_limit_reached",
                                    "message": (
                                        "The maximum number of re-planning attempts has been reached. "
                                        "Start a new trip plan or review the available alternatives."
                                    ),
                                },
                            })
                            continue

                        original_state = json.loads(trip.state_json)
                        try:
                            reservation = ReplanningService.reserve_attempt(
                                db,
                                trip,
                                user.id,
                                key,
                                original_state,
                                max_replan_attempts(),
                            )
                        except ReplanAttemptLimitReached:
                            await websocket.send_json({
                                "type": "replanning",
                                "data": {
                                    "trip_id": trip.id,
                                    "status": "attempt_limit_reached",
                                    "message": (
                                        "The maximum number of re-planning attempts has been reached. "
                                        "Start a new trip plan or review the available alternatives."
                                    ),
                                },
                            })
                            continue
                        except IntegrityError:
                            db.rollback()
                            existing = db.query(TripPlanChange).filter(
                                TripPlanChange.trip_id == trip.id,
                                TripPlanChange.user_id == user.id,
                                TripPlanChange.request_key == key,
                            ).first()
                            if existing is None:
                                raise
                            await websocket.send_json({
                                "type": "replanning",
                                "data": json.loads(existing.response_json),
                            })
                            continue

                        try:
                            result_data = await master_agent.process_request(
                                user_message,
                                status_callback=send_status,
                                saved_preferences=saved_preferences,
                                current_trip=original_state,
                            )
                        except Exception:
                            reservation.status = "failed"
                            failed_response = {
                                "trip_id": trip.id,
                                "replanning": {
                                    "status": "failed",
                                    "message": "The re-planning attempt failed; the saved trip was not changed.",
                                },
                            }
                            reservation.response_json = json.dumps(failed_response)
                            db.commit()
                            raise
                        analysis = ReplanningService.prepare_proposal(
                            original_state,
                            result_data,
                            user_message,
                            saved_preferences,
                        )
                        result_data["trip_id"] = trip.id
                        result_data["replanning"] = analysis
                        proposal_status = (
                            "pending"
                            if analysis["status"] == "awaiting_approval"
                            else analysis["status"]
                        )
                        result_data["replanning"]["proposal_id"] = reservation.id
                        candidate_state = {
                            key: value
                            for key, value in result_data.items()
                            if key not in {"trip_id", "replanning"}
                        }
                        ReplanningService.complete_attempt(
                            db,
                            reservation,
                            candidate_state,
                            analysis,
                            result_data,
                            proposal_status,
                        )
                    else:
                        # Keep the existing chat message and plan response formats.
                        result_data = await master_agent.process_request(
                            user_message,
                            status_callback=send_status,
                            saved_preferences=saved_preferences,
                        )
                        trip = ReplanningService.save_initial_plan(db, user.id, result_data)
                        result_data["trip_id"] = trip.id
                        analysis = ReplanningService.analyze(
                            result_data,
                            saved_preferences,
                        )
                        result_data["replanning"] = {
                            "status": {
                                "conflict_detected": "conflicts_detected",
                                "unable_to_validate": "unable_to_validate",
                                "no_conflict": "not_needed",
                            }[analysis["status"]],
                            "conflicts": analysis["conflicts"],
                            "checks_completed": analysis["checks_completed"],
                            "unable_to_validate": analysis["unable_to_validate"],
                            "changes": [],
                            "alternatives": ReplanningService._budget_alternatives(
                                result_data,
                                saved_preferences,
                            ),
                            "unresolved_conflicts": analysis["conflicts"],
                            "message": (
                                "Review these detected planning issues; no changes were applied."
                                if analysis["status"] == "conflict_detected"
                                else "Validation is incomplete; no conflict is asserted."
                                if analysis["status"] == "unable_to_validate"
                                else "No conflict found in the completed checks."
                            ),
                        }
                    
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
