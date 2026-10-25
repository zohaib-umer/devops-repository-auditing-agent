import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import main, write_json
from auditor.agent import AuditAgent
from auditor.provider import LLMClient
from auditor.repository import Repository
from auditor.safety import Redactor, repository_url
from auditor.schema import validate_report
from tools import InspectionTools


class ObjectRepository:
    """In-memory Git object fixture; no remote answer keys or hardcoded target findings."""
    def __init__(self, files):
        self.files = files

    def git(self, *args):
        if args[0] == "ls-tree":
            return b"".join(f"100644 blob {i}\t{p}\0".encode() for i, p in enumerate(self.files))
        if args[0] == "cat-file":
            content = list(self.files.values())[int(args[2])]
            return str(len(content)).encode() if args[1] == "-s" else content
        if args[0] == "log":
            return b"abc123 2026-01-01 Add CI tests\n"
        raise AssertionError(args)


def call(name, args):
    return {"tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}


class ScriptedClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.received = []

    def complete(self, messages, tools):
        self.received.append(json.loads(json.dumps(messages)))
        return next(self.responses)


def finding(**changes):
    result = {"rule_id": "CI-IGNORED-FAILURE", "category": "ci", "severity": "high",
              "file": ".github/workflows/ci.yml", "line": 2,
              "description": "Test failure is suppressed, allowing the job to pass.",
              "evidence": "pytest || true", "recommendation": "Remove || true so failures fail the CI job."}
    result.update(changes)
    return result


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tools = InspectionTools(ObjectRepository({
            "README.md": b"# Demo\nRun tests using unittest.\n",
            ".github/workflows/ci.yml": b"steps:\n  - run: pytest || true\n",
            "config.py": b"API_KEY = 'sk-abcdefghijklmnopqrstuvwx'\n",
            "image.bin": b"abc\0xyz",
        }))

    def test_pagination(self):
        first = self.tools.list_files(limit=2)
        second = self.tools.list_files(offset=first["next_offset"], limit=2)
        self.assertEqual(len(first["files"] + second["files"]), 4)
        self.assertIsNone(second["next_offset"])

    def test_read_numbers_and_redaction(self):
        result = self.tools.read_file("config.py")
        self.assertEqual(result["lines"][0]["line"], 1)
        self.assertNotIn("sk-abcdef", json.dumps(result))
        self.assertIn("[REDACTED]", json.dumps(result))

    def test_literal_search(self):
        result = self.tools.search_code("|| true", glob="*.yml")
        self.assertEqual(result["matches"][0]["line"], 2)
        self.assertEqual(self.tools.search_code("(a+)+$")["matches"], [])

    def test_missing_binary_and_traversal(self):
        for path in ["../outside", ".git/config", "missing", "image.bin"]:
            self.assertIn("error", self.tools.dispatch("read_file", {"path": path}))

    def test_symlink_and_submodule_rejected(self):
        for mode, kind in [("120000", "blob"), ("160000", "commit")]:
            self.tools.entries["link"] = (mode, kind, "0")
            with self.assertRaises(ValueError):
                self.tools.read_file("link")

    def test_oversized_and_invalid_ranges(self):
        tools = InspectionTools(ObjectRepository({"big": b"a" * 256001}))
        self.assertIn("error", tools.dispatch("read_file", {"path": "big"}))
        self.assertIn("error", self.tools.dispatch("read_file", {"path": "README.md", "start_line": 0}))
        self.assertIn("error", self.tools.dispatch("shell", {"command": "echo unsafe"}))

    def test_history_scope(self):
        self.assertTrue(self.tools.git_log()["shallow"])

    def test_private_key_preserves_lines(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nabcdef\n-----END RSA PRIVATE KEY-----\nnext"
        result = Redactor().text(text)
        self.assertEqual(result.count("\n"), text.count("\n"))
        self.assertNotIn("abcdef", result)


class AgentTests(unittest.TestCase):
    url = "https://github.com/example/fixture"

    def setUp(self):
        self.tools = InspectionTools(ObjectRepository({
            ".github/workflows/ci.yml": b"steps:\n  - run: pytest || true\n"}))

    def sequence(self, final):
        return [call("list_files", {}), call("read_file", {"path": ".github/workflows/ci.yml"}),
                call("git_log", {}), call("finish_audit", {"violations": final})]

    def test_dynamic_followup_and_report(self):
        sequence = self.sequence([finding()])
        sequence.insert(2, call("search_code", {"pattern": "pytest"}))
        client = ScriptedClient(sequence)
        agent = AuditAgent(client, self.tools)
        report = agent.run(self.url)
        self.assertEqual(report["violations"], [finding()])
        self.assertEqual([s["tool"] for s in agent.trace],
                         ["list_files", "read_file", "search_code", "git_log", "finish_audit"])
        self.assertIn("pytest || true", client.received[2][-1]["content"])

    def test_healthy_alternative_zero_findings(self):
        tools = InspectionTools(ObjectRepository({"tox.ini": b"[testenv]\ncommands = python -m unittest discover\n"}))
        client = ScriptedClient([call("list_files", {}), call("read_file", {"path": "tox.ini"}),
                                 call("git_log", {}), call("finish_audit", {"violations": []})])
        self.assertEqual(AuditAgent(client, tools).run(self.url)["violations"], [])

    def test_invalid_evidence_repaired(self):
        sequence = self.sequence([finding(evidence="invented")])
        sequence.append(call("finish_audit", {"violations": [finding()]}))
        agent = AuditAgent(ScriptedClient(sequence), self.tools)
        self.assertEqual(len(agent.run(self.url)["violations"]), 1)
        self.assertIn("error", agent.trace[3]["result"])

    def test_schema_and_deduplication(self):
        self.tools.list_files()
        self.tools.read_file(".github/workflows/ci.yml")
        report = validate_report({"violations": [finding(), finding()]}, self.url, self.tools)
        self.assertEqual(len(report["violations"]), 1)
        for changes in [{"severity": "urgent"}, {"line": True}, {"line": 99}, {"file": "nope"}]:
            with self.assertRaises(ValueError):
                validate_report({"violations": [finding(**changes)]}, self.url, self.tools)

    def test_incomplete_discovery_rejects_absence(self):
        with self.assertRaises(ValueError):
            validate_report({"violations": [finding(file=None, line=None)]}, self.url, self.tools)

    def test_budget_failure(self):
        client = ScriptedClient([call("list_files", {})] * 4)
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            AuditAgent(client, self.tools, max_steps=4).run(self.url)

    def test_malformed_response_fatal(self):
        with self.assertRaises(RuntimeError):
            AuditAgent(ScriptedClient([{"content": "done"}]), self.tools).run(self.url)

    def test_unknown_tool_is_observed_then_recovered(self):
        client = ScriptedClient([call("execute_shell", {})] + self.sequence([]))
        agent = AuditAgent(client, self.tools)
        agent.run(self.url)
        self.assertIn("error", agent.trace[0]["result"])


class InterfaceTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(repository_url("https://github.com/owner/repo.git"), "https://github.com/owner/repo")
        for url in ["file:///tmp/test", "https://evil.com/a/b", "https://github.com/a/b?x=1",
                    "https://user@github.com/a/b", "https://github.com/a/b/tree/main", "https://github.com/a/.."]:
            with self.assertRaises(ValueError):
                repository_url(url)

    def test_missing_credentials_and_failure_no_report(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True), tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                LLMClient()
            output = Path(directory) / "report.json"
            self.assertEqual(main(["--repo", "https://github.com/a/b", "--output", str(output)]), 1)
            self.assertFalse(output.exists())

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            write_json(path, {"violations": []})
            self.assertEqual(json.loads(path.read_text()), {"violations": []})
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_real_git_objects_are_read_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / "README.md").write_text("# Real Git fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture"], check=True)
            repo = Repository("https://github.com/example/fixture")
            repo.root = root / ".git"
            repo.env = os.environ.copy()
            before = (root / "README.md").read_bytes()
            tools = InspectionTools(repo)
            self.assertEqual(tools.read_file("README.md")["lines"][0]["text"], "# Real Git fixture")
            self.assertIn("Fixture", tools.git_log()["history"])
            self.assertEqual((root / "README.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
