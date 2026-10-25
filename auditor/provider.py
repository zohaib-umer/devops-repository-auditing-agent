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
        self.provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
        if self.provider not in {"ollama", "openai"}:
            raise ValueError("LLM_PROVIDER must be ollama or openai")
        local = self.provider == "ollama"
        self.key = "" if local else os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b") if local else os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        self.base = (os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") if local
                     else os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        parsed = urlsplit(self.base)
        loopback = local and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not (parsed.scheme == "https" or loopback) or not parsed.hostname
                or parsed.username or parsed.query or parsed.fragment):
            raise ValueError("API base must use HTTPS, or loopback HTTP for local Ollama, without credentials/query/fragment")
        if not local and not self.key:
            raise ValueError("Set OPENAI_API_KEY in your environment before running an audit")
        self.opener = build_opener(NoRedirect())

    def complete(self, messages, tools):
        payload = {"model": self.model, "messages": messages, "tools": tools}
        local = self.provider == "ollama"
        if local:
            native_messages = json.loads(json.dumps(messages))
            for message in native_messages:
                if message.get("content") is None:
                    message["content"] = ""
                for call in message.get("tool_calls", []):
                    arguments = call["function"]["arguments"]
                    if isinstance(arguments, str):
                        call["function"]["arguments"] = json.loads(arguments)
            # Some Ollama model templates render tool definitions only when
            # the last message is user-role. Keep them visible on every turn.
            native_messages.append({"role": "user", "content":
                "Continue the repository audit using the observations above. Choose exactly one tool call. "
                "Discover files before guessing paths; inspect relevant files and git history. "
                "When the evidence is sufficient, call finish_audit with supported violations only."})
            payload["messages"] = native_messages
            payload.update(stream=False, think=False, options={"temperature": 0, "num_ctx": 32768})
        else:
            payload.update(tool_choice="required", parallel_tool_calls=False)
        body = json.dumps(payload).encode()
        for attempt in range(3):
            headers = {"Content-Type": "application/json"}
            if self.key:
                headers["Authorization"] = "Bearer " + self.key
            request = Request(self.base + ("/api/chat" if local else "/chat/completions"), data=body, headers=headers)
            try:
                with self.opener.open(request, timeout=600 if local else 60) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RuntimeError("LLM response exceeds size limit")
                data = json.loads(raw)
                message = data["message"] if local else data["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError("Malformed response")
                if local:
                    for index, call in enumerate(message.get("tool_calls", [])):
                        call.setdefault("id", f"ollama_call_{index}")
                        call.setdefault("type", "function")
                        arguments = call["function"]["arguments"]
                        if isinstance(arguments, dict):
                            call["function"]["arguments"] = json.dumps(arguments)
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
            except (KeyError, IndexError, ValueError, TypeError):
                raise RuntimeError("LLM provider returned an invalid response") from None
