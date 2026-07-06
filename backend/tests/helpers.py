"""
Shared test helpers — imported by integration test modules.
"""
import json
from unittest.mock import AsyncMock, MagicMock


def make_mock_llm_client(response_dict: dict) -> AsyncMock:
    """Build a mock Anthropic client that returns response_dict as JSON content."""
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(response_dict))]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=msg)
    return client
