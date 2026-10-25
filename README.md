# DevOps Repository Auditing Agent

[![Validate auditor](https://github.com/zohaib-umer/devops-repository-auditing-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zohaib-umer/devops-repository-auditing-agent/actions/workflows/ci.yml)

A Python AI agent that investigates public GitHub repositories, identifies evidence-backed DevOps defects, and produces actionable JSON reports. **Runs locally with Ollama; no paid API key is required.**

The LLM chooses inspection tools and follow-up calls. Python handles dispatch, input validation, safe repository access and report validation. No prebuilt agent or orchestration framework is used.

## Quick start

Requirements: Python 3.11+, Git, and [Ollama](https://ollama.com/download). Install Ollama, then open a terminal in this repository:

```sh
ollama pull llama3.2:3b
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Ensure Ollama is running. If its desktop app is not running, start `ollama serve` in another terminal. Then execute the required interface:

```sh
python agent.py --repo https://github.com/octocat/Hello-World --output report.json
```

Include the investigation trace with:

```sh
python agent.py --repo https://github.com/octocat/Hello-World --output report.json --trace trace.json --max-steps 60
```

The agent runs unattended. Local CPU inference can take several minutes. The Python runtime uses only the standard library; `requirements.txt` documents this. Optional installation using `python -m pip install -e .` also provides the `devops-audit` command.

## Configuration

`.env.example` lists supported variables. Export them in your shell; `.env` files are not automatically loaded.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | `ollama` for the native local API, or `openai` for compatible hosted services |
| `OLLAMA_MODEL` | `llama3.2:3b` | Installed model supporting function calls |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama service |
| `OLLAMA_TOOL_MODE` | `structured` | Schema-constrained JSON tool decisions; `native` optionally uses model-specific tool templates |
| `OPENAI_API_KEY` | unset | Required only with the hosted provider |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Hosted provider model identifier |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Hosted API root |

To select a different installed Ollama model:

```powershell
$env:OLLAMA_MODEL = 'llama3.2:3b'
```

On macOS/Linux use `export OLLAMA_MODEL=llama3.2:3b`. The native Ollama request disables thinking, sets temperature to zero and requests a 32,768-token context. Use a machine with sufficient memory for the chosen model and context.

For a hosted provider, set `LLM_PROVIDER=openai` and export the corresponding key, model and HTTPS endpoint. Never commit credentials. Local Ollama requests do not send a hosted API key.

## Architecture

```text
Public GitHub URL
       |
Validate URL -> temporary bare Git clone at a fixed commit
       |
LLM observes evidence and chooses a tool
       |
Allowlisted Python dispatcher executes a read-only inspection
       |
Redacted results return to the LLM -> follow-up investigation
       |
finish_audit -> schema and evidence validation
       |                    |
  accepted report       correction feedback to LLM
       |
Atomic JSON output + optional trace
```

The agent implements Observe → Reason → Select Tool → Execute Tool → Interpret Results → Report. Tool order and arguments are chosen dynamically by the LLM, not by a fixed checklist. Before finishing, it must have inspected the tree, read a file and examined commit history. Invalid line evidence is returned to the model for correction. By default Ollama uses a JSON schema containing all tool names and their argument contracts to constrain each decision. The model chooses the tool; Python dispatches it and returns observations for the next decision. Native function-call mode is also available. This avoids depending on small models' native tool templates while preserving genuine LLM-directed investigation.

## Inspection tools

| Tool | Behavior |
| --- | --- |
| `list_files()` | Discover tracked paths; paginated, up to 500 files per call |
| `read_file(path)` | Read redacted source with one-based line numbers; up to 200 lines and 16,000 characters per call |
| `search_code(pattern)` | Case-insensitive literal search with optional file glob; up to 200 files per page |
| `git_log()` | Inspect up to 50 recent commit subjects from a shallow history |

Tool output includes pagination and unavailable-file information. Search offsets count files, not matches. When the match limit is reached, inspect the last matching file directly, then advance the offset by one. Symlinks, submodules, binary files, non-UTF-8 text and files over 256 KB are not read as source. The clone contains at most 100 commits from the default branch.

## DevOps coverage

The investigation policy covers all nine assignment areas:

| Area | Examples investigated |
| --- | --- |
| Repository hygiene | Relevant documentation gaps, tracked generated or local files |
| Dependency management | Conflicting specifications, inappropriate constraints, install/lockfile inconsistencies |
| Code quality | Quality controls and concrete maintainability problems |
| Testing | Test discovery, documented test commands and CI execution |
| CI | Trigger coverage, ignored failures, ineffective conditions and gates |
| Security | Credentials, unsafe permissions, untrusted checkout/execution and shell injection |
| Configuration | Developer-specific paths, committed local settings, environment separation |
| Collaboration | Unsafe PR automation and relevant review guidance |
| Build and release | Build validation and risky deployment/release paths |

The model must investigate acceptable alternatives before reporting missing controls. An absent optional tool or file is not proof of a defect. Placeholder credentials and test fixtures require context. Remote branch protection cannot be inferred from source files. ML training, datasets and model validation are outside the scope.

## JSON output contract

Every completed report has exactly `repository` and `violations` at the top level. Each violation contains all of these fields:

| Field | Contract |
| --- | --- |
| `rule_id` | Stable descriptive identifier |
| `category` | One of the nine categories defined in `auditor/schema.py` |
| `severity` | `critical`, `high`, `medium`, or `low` |
| `file` | Relative tracked path, or JSON `null` when inapplicable |
| `line` | Positive one-based integer, or JSON `null` when inapplicable |
| `description` | Concrete defect and impact |
| `evidence` | Observed redacted source excerpt or supported structural evidence |
| `recommendation` | Actionable remediation |

Line-based evidence must exactly quote a nonempty substring of the observed line. File-level findings require discovered paths; repository-wide absence findings require a complete file listing. Duplicate rule/file/line findings are removed. A completed inspection may correctly produce an empty `violations` array.

Exit code 0 indicates a completed report. Fatal failures return 1 with a useful stderr message; invalid CLI syntax returns 2. A failed run never creates a fabricated completed report. Existing output files are preserved on failure, so check the exit code. Report writes are atomic. Optional traces contain the inspected commit, selected model, tool arguments/results and completion status.

## Safety and reliability

Targets are never checked out, modified, imported, installed, built or executed. The tools read Git objects in a temporary bare clone that is cleaned up afterward. Git user/system configuration, credential prompts/helpers, redirects and non-HTTPS protocols are disabled. Paths must belong to the inspected commit; symlinks and submodules are not followed.

The LLM cannot invoke a shell or arbitrary tool. Repository content and commit messages are untrusted data. Common credential formats, sensitive assignments, private keys, URL passwords and known environment secrets are redacted before entering model context or traces. Redaction is heuristic; review traces before publishing them. With local Ollama, inspection content stays on the configured local inference service.

Defaults: 60 model turns, configurable from 4 to 150; 600 seconds per local inference request or 60 seconds per hosted request; at most two retries for transient network errors; 120-second Git clone timeout and 30-second inspection-command timeout. Large tree metadata and accumulated context have explicit size limits. Clone disk usage is not quota-enforced; use OS/container resource limits for very large or hostile repositories.

## Project structure

```text
agent.py                    Required CLI and atomic output handling
tools.py                    Inspection tools and allowlisted dispatch
auditor/agent.py            Explicit model/tool loop and investigation policy
auditor/provider.py         Native Ollama and hosted API clients
auditor/repository.py       Read-only Git object access and clone lifecycle
auditor/schema.py           Tool contracts and report/evidence validation
auditor/safety.py           URL validation and secret redaction
tests/                      Offline unit and integration tests
.github/workflows/ci.yml    Linux/Windows tests, lint and package builds
.env.example                Configuration names without real credentials
requirements.txt            Standard-library-only runtime declaration
pyproject.toml              Packaging and lint configuration
```

## Tests and CI

```sh
python -m unittest discover -s tests -v
python -m pip install ruff==0.11.13 build==1.2.2.post1
python -m ruff check .
python -m build
```

Automated tests cover dynamic follow-up, tool result interpretation, correction of unsupported evidence, healthy output, pagination, literal search, secret redaction, symlink/binary/path protections, real Git reads, schema compliance, deduplication, atomic output, bounded retries, API errors, native Ollama tool-call normalization and HTTP endpoint restrictions. Scripted provider responses in tests verify software behavior, not model accuracy.

CI is configured for Ubuntu and Windows with Python 3.11 and 3.13, read-only GitHub permissions, pull-request and main-branch triggers, and pinned action revisions. API credentials are not needed for tests.

## Sample audit and verification

Generate an authentic sample with the local model:

```sh
python agent.py --repo https://github.com/octocat/Hello-World --output sample_report.json --trace sample_trace.json
```

The sample trace records the exact target commit and actual tool sequence. A minimal demonstration repository can legitimately yield no findings; its absence of a production CI pipeline is not automatically a defect. Do not invent findings to make the example look more impressive.

Verification on October 25, 2026: **30 automated tests passed**, Ruff passed, and source/wheel package builds succeeded. GitHub CI passed on Linux and Windows with Python 3.11 and 3.13.

**Live Ollama run completed successfully:** `llama3.2:3b` audited `octocat/Hello-World` at commit `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`, using the auditor implementation at commit `85a8c63`. The committed `sample_report.json` is the authentic output, with an empty violations array. `sample_trace.json` records all 15 decisions, including rejected unsupported candidate claims and subsequent inspection. Rejected candidates in the trace are model proposals, not accepted findings. This sample demonstrates actual local inference, dynamic tool calls, correction feedback and valid JSON output; it is not a broad accuracy benchmark.

For broader evaluation, use public repositories with independently documented obvious defects, subtle CI/security defects and healthy alternatives. Calculate precision as true positives divided by reported findings and recall as true positives divided by known defects. Treat undefined ratios as N/A. No hidden-test scores or unmeasured accuracy claims are made.

## Troubleshooting

- Connection refused: start the Ollama desktop app or `ollama serve`; verify `ollama list` works.
- Model missing: run `ollama pull llama3.2:3b` or select an installed tool-capable model.
- Slow local run: allow CPU inference time, close memory-heavy applications, or choose a suitable model for your hardware.
- Tool budget exhausted: inspect the incomplete trace; choose a stronger tool-capable model or increase the budget.
- Git operation failed: verify public repository access, Git installation and network access.
- Hosted authentication failure: configure the hosted key/model only when `LLM_PROVIDER=openai`.

## Assignment submission

Public repository: https://github.com/zohaib-umer/devops-repository-auditing-agent

To download, use GitHub's **Code → Download ZIP**, or clone:

```sh
git clone https://github.com/zohaib-umer/devops-repository-auditing-agent.git
cd devops-repository-auditing-agent
```

Submit **only this public GitHub repository URL** through the course submission channel. All setup, architecture, execution and related project information is included in this README.

| Assignment deliverable | Included |
| --- | --- |
| Public GitHub repository | This repository |
| Required CLI | `agent.py --repo <url> --output report.json` |
| Four required inspection tools | `tools.py` |
| Explicit LLM-directed agent loop | `auditor/agent.py` |
| Local LLM execution | Ollama integration, verified with `llama3.2:3b` |
| Dependencies and configuration | `requirements.txt`, `pyproject.toml`, `.env.example` |
| Setup, architecture and execution documentation | This README |
| Automated tests | `tests/`, 30 passing tests |
| Authentic example output | `sample_report.json` |
| Inspectable tool-call evidence | `sample_trace.json` |
| CI and meaningful commit history | GitHub Actions and repository commits |

The required implementation and submission artifacts are complete. Hidden-repository grading remains the instructor's independent evaluation.

## Contribution and review

Use focused commits and pull requests explaining the change and validation. Review evidence grounding, safe tool boundaries, prompt-injection handling and redaction carefully. Add regression tests for new behavior and preserve the required CLI/JSON contract. Never commit real credentials.

## References

- [Ollama API documentation](https://docs.ollama.com/api)
- [Git cat-file](https://git-scm.com/docs/git-cat-file)
- [Git ls-tree](https://git-scm.com/docs/git-ls-tree)
