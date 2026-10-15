"""Command-line entry point: python agent.py --repo URL --output report.json."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from auditor.agent import AuditAgent
from auditor.provider import LLMClient
from auditor.repository import Repository
from auditor.safety import Redactor, repository_url
from tools import InspectionTools


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".audit-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LLM-directed, read-only public GitHub DevOps auditor")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace", help="Optional redacted JSON tool trace and commit provenance")
    parser.add_argument("--max-steps", type=int, default=60)
    args = parser.parse_args(argv)
    redactor, agent, commit = Redactor(), None, None
    try:
        url = repository_url(args.repo)
        if not 4 <= args.max_steps <= 150:
            raise ValueError("--max-steps must be between 4 and 150")
        if args.trace and Path(args.trace).resolve() == Path(args.output).resolve():
            raise ValueError("Report and trace paths must differ")
        client = LLMClient()
        with Repository(url) as repo:
            commit = repo.commit
            agent = AuditAgent(client, InspectionTools(repo, redactor), args.max_steps)
            report = agent.run(url)
        if args.trace:
            write_json(args.trace, redactor.clean({"repository": url, "commit": commit,
                       "model": client.model, "completed": True, "steps": agent.trace}))
        write_json(args.output, report)
        print(f"Audit complete: {len(report['violations'])} verified findings", file=sys.stderr)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        if args.trace and agent:
            try:
                write_json(args.trace, redactor.clean({"commit": commit, "completed": False, "steps": agent.trace}))
            except OSError:
                pass
        print("Audit failed: " + redactor.text(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
