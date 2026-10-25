import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from auditor.provider import LLMClient, NoRedirect


class ProviderTests(unittest.TestCase):
    def client(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-placeholder",
                                     "OPENAI_BASE_URL": "https://api.example.invalid/v1"}):
            return LLMClient()

    def response(self):
        return io.BytesIO(json.dumps({"choices": [{"message": {
            "role": "assistant", "tool_calls": []}}]}).encode())

    def test_function_call_request(self):
        client = self.client()
        client.opener = Mock()
        client.opener.open.return_value = self.response()
        self.assertEqual(client.complete([], [])["role"], "assistant")
        request = client.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(request.full_url, "https://api.example.invalid/v1/chat/completions")

    @patch("auditor.provider.time.sleep")
    def test_rate_limit_retries(self, sleep):
        client = self.client()
        client.opener = Mock()
        error = HTTPError(client.base, 429, "private response", {}, None)
        client.opener.open.side_effect = [error, self.response()]
        client.complete([], [])
        self.assertEqual(client.opener.open.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("auditor.provider.time.sleep")
    def test_network_failure_has_bounded_retries(self, sleep):
        client = self.client()
        client.opener = Mock()
        client.opener.open.side_effect = URLError("sensitive server details")
        with self.assertRaisesRegex(RuntimeError, "network request failed") as caught:
            client.complete([], [])
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(client.opener.open.call_count, 3)

    def test_auth_failure_does_not_echo_response(self):
        client = self.client()
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(client.base, 401, "secret body", {}, None)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
            client.complete([], [])
        self.assertNotIn("secret body", str(caught.exception))
        self.assertEqual(client.opener.open.call_count, 1)

    def test_malformed_provider_payload(self):
        client = self.client()
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(b"not JSON")
        with self.assertRaisesRegex(RuntimeError, "invalid response"):
            client.complete([], [])

    def test_no_credential_forwarding_on_redirect(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_insecure_endpoint_rejected(self):
        for url in ["http://api.example.invalid", "https://user:pass@api.example.invalid", "https://api.example.invalid?x=1"]:
            with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test", "OPENAI_BASE_URL": url}):
                with self.assertRaises(ValueError):
                    LLMClient()

    def test_ollama_native_tool_response(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:11434"}):
            client = LLMClient()
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(json.dumps({"message": {
            "role": "assistant", "tool_calls": [{"function": {
                "name": "read_file", "arguments": {"path": "README.md"}}}]}}).encode())
        history = [{"role": "assistant", "content": None, "tool_calls": [{"function": {
            "name": "list_files", "arguments": "{}"}}]}]
        response = client.complete(history, [])
        self.assertEqual(json.loads(response["tool_calls"][0]["function"]["arguments"]), {"path": "README.md"})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("tool_choice", json.loads(request.data))
        self.assertEqual(json.loads(request.data)["messages"][0]["tool_calls"][0]["function"]["arguments"], {})
        self.assertEqual(history[0]["tool_calls"][0]["function"]["arguments"], "{}")

    def test_ollama_remote_http_rejected(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://example.com:11434"}):
            with self.assertRaises(ValueError):
                LLMClient()


if __name__ == "__main__":
    unittest.main()
