"""Minimal OpenAI-compatible HTTPS client, with no orchestration framework."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LLMClient:
    def __init__(self):
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("OPENAI_BASE_URL must be an HTTPS API base without credentials, query or fragment")
        if not self.key:
            raise ValueError("Set OPENAI_API_KEY in your environment before running an audit")
        self.opener = build_opener(NoRedirect())

    def complete(self, messages, tools):
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "tool_choice": "required", "parallel_tool_calls": False}).encode()
        for attempt in range(3):
            request = Request(self.base + "/chat/completions", data=body,
                              headers={"Authorization": "Bearer " + self.key,
                                       "Content-Type": "application/json"})
            try:
                with self.opener.open(request, timeout=60) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RuntimeError("LLM response exceeds size limit")
                data = json.loads(raw)
                message = data["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError("Malformed response")
                return message
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"LLM request failed (HTTP {exc.code}); check credentials, model, quota and endpoint") from None
            except (URLError, TimeoutError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("LLM network request failed or timed out") from None
            except (KeyError, IndexError, ValueError):
                raise RuntimeError("LLM provider returned an invalid response") from None
