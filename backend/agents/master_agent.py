import os
import json
import logging
from .schemas import MasterAgentResponse, Dates, Budget

logger = logging.getLogger(__name__)

# The system prompt instructs the LLM to act as an orchestrator only
SYSTEM_PROMPT = """You are a Master Travel Agent Orchestrator. Your ONLY job is to analyze a user's travel request and return a structured JSON plan.

You must extract the following from the user's message:
- request_summary: A brief one-line summary of the user's request.
- destination: The travel destination (or null if not provided).
- origin: Where they are traveling from (or null if not provided).
- dates: An object with "start" and "end" date strings (or null if not provided).
- duration_days: Number of days for the trip (or null if not provided).
- travelers: Number of travelers (or null if not provided).
- budget: An object with "amount" (number or null) and "currency" (default "INR").
- preferences: A list of interests/preferences mentioned (e.g. ["adventure", "beaches"]).
- tasks: A list of planning tasks required. Choose from: "find_transport", "find_hotels", "find_activities", "create_itinerary", "check_budget", "check_weather".
- missing_information: A list of important missing details the user did not provide (e.g. "origin", "budget", "travel dates or duration", "number of travelers").

Rules:
1. Do NOT invent information the user did not provide. Use null for unknown fields.
2. If critical info is missing, add it to the missing_information list.
3. Always include relevant tasks from the allowed task list.
4. Return ONLY valid JSON matching the schema. No extra text or markdown."""

# The JSON schema we ask the LLM to follow
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "request_summary": {"type": "string"},
        "destination": {"type": ["string", "null"]},
        "origin": {"type": ["string", "null"]},
        "dates": {
            "type": "object",
            "properties": {
                "start": {"type": ["string", "null"]},
                "end": {"type": ["string", "null"]}
            }
        },
        "duration_days": {"type": ["integer", "null"]},
        "travelers": {"type": ["integer", "null"]},
        "budget": {
            "type": "object",
            "properties": {
                "amount": {"type": ["number", "null"]},
                "currency": {"type": "string"}
            }
        },
        "preferences": {"type": "array", "items": {"type": "string"}},
        "tasks": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["request_summary", "destination", "origin", "dates", "duration_days",
                  "travelers", "budget", "preferences", "tasks", "missing_information"]
}


class MasterAgent:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.client = None

        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
                logger.info("Groq LLM client initialized successfully.")
            except ImportError:
                logger.warning("groq package not installed. Running in mock mode. Run: pip install groq")
            except Exception as e:
                logger.warning(f"Failed to initialize Groq client: {e}. Running in mock mode.")
        else:
            logger.warning("No GROQ_API_KEY found in environment. MasterAgent running in mock mode.")

    def process_request(self, user_message: str) -> MasterAgentResponse:
        """Main entry point: analyze a user's travel request and return a structured plan."""
        logger.info(f"Master Agent received request: {user_message}")

        if not user_message or not user_message.strip():
            raise ValueError("Empty message. Please describe your travel plans.")

        if self.client:
            return self._call_llm(user_message)
        else:
            return self._mock_response(user_message)

    def _call_llm(self, user_message: str) -> MasterAgentResponse:
        """Call the Groq LLM API to parse the user's request into a structured plan."""
        try:
            logger.info("Calling Groq LLM API...")

            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1024
            )

            raw_text = response.choices[0].message.content
            logger.info("LLM response received. Parsing JSON...")

            data = json.loads(raw_text)

            # Safely build the response using Pydantic
            plan = MasterAgentResponse(
                request_summary=data.get("request_summary", ""),
                destination=data.get("destination"),
                origin=data.get("origin"),
                dates=Dates(
                    start=data.get("dates", {}).get("start"),
                    end=data.get("dates", {}).get("end")
                ),
                duration_days=data.get("duration_days"),
                travelers=data.get("travelers"),
                budget=Budget(
                    amount=data.get("budget", {}).get("amount"),
                    currency=data.get("budget", {}).get("currency", "INR")
                ),
                preferences=data.get("preferences", []),
                tasks=data.get("tasks", []),
                missing_information=data.get("missing_information", [])
            )

            logger.info("Master Agent plan generated successfully.")
            return plan

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            raise Exception("AI returned an invalid response. Please try again.")
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise Exception("Failed to process your request with the AI provider. Please try again.")

    def _mock_response(self, user_message: str) -> MasterAgentResponse:
        """Fallback mock response when no LLM API key is configured."""
        logger.info("Generating mock response (no API key configured)")
        msg = user_message.lower()

        # Simple keyword extraction for testing
        destination = None
        for place in ["goa", "paris", "manali", "jaipur", "mumbai", "london", "tokyo"]:
            if place in msg:
                destination = place.title()
                break
        if not destination:
            destination = "Unknown Destination"

        origin = "Delhi" if "delhi" in msg else None

        duration = None
        for i in range(1, 31):
            if f"{i} day" in msg:
                duration = i
                break

        travelers = None
        for i in range(1, 11):
            if f"{i} people" in msg or f"{i} person" in msg:
                travelers = i
                break

        budget_amount = None
        if "50000" in msg or "50,000" in msg:
            budget_amount = 50000.0
        elif "budget" in msg:
            budget_amount = None  # mentioned but no number

        # Determine missing info
        missing = []
        if not origin:
            missing.append("origin")
        if not duration:
            missing.append("travel dates or duration")
        if not travelers:
            missing.append("number of travelers")
        if budget_amount is None:
            missing.append("budget")

        # Determine tasks
        tasks = ["find_transport", "find_hotels", "find_activities", "create_itinerary", "check_budget", "check_weather"]

        return MasterAgentResponse(
            request_summary=f"User wants to travel to {destination}" + (f" from {origin}" if origin else ""),
            destination=destination,
            origin=origin,
            dates=Dates(),
            duration_days=duration,
            travelers=travelers,
            budget=Budget(amount=budget_amount),
            preferences=[],
            tasks=tasks,
            missing_information=missing
        )
