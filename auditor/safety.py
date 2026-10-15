"""Input validation and best-effort secret minimization."""

import os
import re
from urllib.parse import urlsplit


def repository_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or parsed.query or parsed.fragment):
        raise ValueError("Repository must be an HTTPS github.com owner/repository URL")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        raise ValueError("Expected https://github.com/owner/repository")
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if owner in {".", ".."} or repo in {"", ".", ".."}:
        raise ValueError("Invalid repository name")
    return f"https://github.com/{owner}/{repo}"


class Redactor:
    def __init__(self):
        self.values = {v for k, v in os.environ.items()
                       if re.search(r"KEY|TOKEN|PASSWORD|SECRET", k, re.I) and len(v) >= 8}

    def text(self, text):
        # Preserve line numbers, including in multiline private keys.
        text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                      lambda m: "[REDACTED PRIVATE KEY]" + "\n" * m[0].count("\n"),
                      text, flags=re.S)
        patterns = [
            r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b",
            r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*[\"']?([^\s\"',;#}{]+)",
            r"https?://[^\s/:]+:([^\s/@]+)@",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                value = match.group(1) if match.lastindex else match[0]
                if len(value) >= 4 and not value.startswith(("$", "<", "[REDACTED")):
                    self.values.add(value)
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        return text

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items()}
        return value
