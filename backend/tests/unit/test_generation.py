"""Offline checks for incomplete Bedrock responses."""

import unittest
from unittest.mock import patch

from app.clients.generation import GenerationTruncatedError, generate_text


class GenerationTests(unittest.TestCase):
    @patch("app.clients.generation.create_bedrock_client")
    def test_truncation_is_not_returned_as_a_complete_answer(self, client):
        client.return_value.converse.return_value = {
            "stopReason": "max_tokens",
            "output": {"message": {"content": [{"text": '{"answer": "partial'}]}},
        }
        with self.assertRaises(GenerationTruncatedError):
            generate_text("Explain")

    @patch("app.clients.generation.create_bedrock_client")
    def test_complete_response_is_returned(self, client):
        client.return_value.converse.return_value = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": "Complete answer"}]}},
        }
        self.assertEqual("Complete answer", generate_text("Explain"))
