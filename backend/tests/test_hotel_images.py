import unittest
from unittest.mock import AsyncMock

from agents.hotel_agent import HotelAgent
from agents.schemas import Budget, Dates, MasterAgentResponse


class HotelImageMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_only_secure_provider_image_urls(self):
        agent = HotelAgent()
        agent.api_client.search_hotels = AsyncMock(return_value={
            "properties": [{
                "name": "Provider Hotel",
                "images": [
                    {"thumbnail": "https://images.example.test/hotel.jpg"},
                    {"thumbnail": "http://images.example.test/insecure.jpg"},
                    {"thumbnail": "javascript:alert(1)"},
                    {"original_image": "https://images.example.test/full.jpg"},
                ],
            }]
        })
        plan = MasterAgentResponse(
            request_summary="Search hotels",
            destination="Singapore",
            dates=Dates(start="2027-05-01", end="2027-05-03"),
            travelers=2,
            budget=Budget(currency="INR"),
            tasks=["find_hotel"],
        )

        result = await agent.execute(plan)

        self.assertEqual(result.status, "success")
        self.assertEqual(
            result.options[0].images,
            [
                "https://images.example.test/hotel.jpg",
                "https://images.example.test/full.jpg",
            ],
        )

    async def test_missing_provider_images_remain_empty(self):
        agent = HotelAgent()
        agent.api_client.search_hotels = AsyncMock(return_value={
            "properties": [{"name": "No Photo Hotel"}]
        })
        plan = MasterAgentResponse(
            request_summary="Search hotels",
            destination="Singapore",
            dates=Dates(start="2027-05-01", end="2027-05-03"),
            travelers=2,
            budget=Budget(currency="INR"),
            tasks=["find_hotel"],
        )

        result = await agent.execute(plan)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.options[0].images, [])


if __name__ == "__main__":
    unittest.main()
