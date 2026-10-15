"""Read Git objects without checking out or executing target code."""

import os
import subprocess
import tempfile
from pathlib import Path

from auditor.safety import repository_url


class Repository:
    def __init__(self, url, timeout=120):
        self.url = repository_url(url)
        self.timeout = timeout
        self.temp = None

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devops-audit-")
        self.root = Path(self.temp.name) / "repo.git"
        # Neutralize user Git configuration, credential helpers, hooks, filters,
        # submodules and protocol rewrites. No target worktree is ever created.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
        try:
            self._run(["clone", "--bare", "--depth=100", "--single-branch",
                       "--no-tags", "--", self.url, str(self.root)], timeout=self.timeout)
            self.commit = self.git("rev-parse", "HEAD").decode().strip()
            return self
        except Exception:
            self.temp.cleanup()
            raise

    def _run(self, args, timeout=30):
        command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                   "-c", "http.followRedirects=false"] + args
        try:
            result = subprocess.run(command, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout, check=False)
        except FileNotFoundError:
            raise RuntimeError("Git is required; install Git and add it to PATH") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("Git operation timed out") from None
        if result.returncode:
            # Remote error text can contain arbitrary repository content.
            raise RuntimeError("Git operation failed; verify public repository access and network")
        return result.stdout

    def git(self, *args):
        return self._run(["--git-dir", str(self.root), *args])

    def __exit__(self, *args):
        if self.temp:
            self.temp.cleanup()
