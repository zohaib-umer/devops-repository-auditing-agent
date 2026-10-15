# DevOps Repository Auditing Agent

An explicit Python agent that lets an LLM investigate a public GitHub repository, select read-only tools, follow evidence, and return an actionable JSON audit. It uses no LangChain, LangGraph, n8n, or other agent framework.

## Project status

The implementation has passed 27 automated tests, Ruff lint, and source/wheel package builds locally on Windows using Python 3.12. Read-only clone, file discovery, file reads and commit-history inspection were also verified against `octocat/Hello-World` at commit `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d` on October 15, 2026. The GitHub CI matrix is configured but has not yet run remotely.

A live LLM audit and public GitHub publication require configured credentials. **A genuine `sample_report.json` has not yet been generated.** Scripted test responses are test doubles, not a substitute for a real sample or evidence of model accuracy. Before submission, complete the live sample and publishing steps below.

## Prerequisites and installation

- Python 3.11 or newer.
- Git on PATH (check `git --version`).
- Internet access to GitHub and an OpenAI-compatible HTTPS API supporting Chat Completions function calls.
- An API key and an accessible model. The default model identifier is `gpt-4.1-mini`; set `OPENAI_MODEL` for your account/provider.

After cloning this repository, open its folder and run:

```sh
python -m venv .venv
```

Activate on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY = '<your API key>'
$env:OPENAI_MODEL = 'gpt-4.1-mini'
```

Activate on macOS/Linux:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
export OPENAI_API_KEY='<your API key>'
export OPENAI_MODEL='gpt-4.1-mini'
```

The runtime has zero third-party Python dependencies. `requirements.txt` documents that choice. Optional editable installation: `python -m pip install -e .` provides the `devops-audit` command. `.env.example` documents variables; `.env` is intentionally not loaded. Export variables in the shell that launches Python. Never commit real keys.

For another compatible provider, set `OPENAI_BASE_URL` to its HTTPS API root (normally ending in `/v1`) and use that provider's model identifier and API key. Repository content is sent to this selected endpoint after best-effort redaction. Do not use a provider you do not trust.

## Required execution interface

```sh
python agent.py --repo https://github.com/pallets/flask --output report.json
```

For provenance and review of dynamic tool selection:

```sh
python agent.py --repo https://github.com/pallets/flask --output report.json --trace trace.json --max-steps 60
```

No interactive prompts occur. Exit code 0 means a completed, validated report, including an empty violations array when appropriate. Exit code 1 means a fatal failure; argument syntax errors use argparse's exit code 2. On failure no new report is written; an existing report at the same path is preserved, so always check the exit code before consuming it. Trace files identify incomplete audits with `completed: false` where the loop started. CLI progress/errors go to stderr.

The default 60-call budget can be set from 4 to 150. Each LLM request times out after 60 seconds and transient HTTP/network failures receive at most two retries. Git clone times out at 120 seconds; individual inspection commands time out at 30 seconds. An audit may take several minutes and incur provider charges; cost depends on inspected content, tool calls and model pricing.

## Architecture and agent behavior

```text
CLI validates URL and configuration
          |
Temporary bare clone, fixed HEAD commit, no checkout
          |
LLM observes -> reasons -> selects one tool
          |                         ^
Allowlisted dispatch -> bounded, redacted evidence
          |
LLM selects finish_audit -> evidence/schema validation
          |                     |
       accepted             correction feedback
          |
Atomic JSON report + optional redacted tool trace
```

The LLM chooses the order and arguments of every inspection call. There is no fixed scanner whose results are simply summarized. The loop passes each tool result back to the model; unsupported final evidence produces corrective feedback and allows further investigation. A minimal completion gate requires structure discovery, a file read and history inspection; it does not prescribe an investigation sequence or predetermined findings.

| File | Responsibility |
| --- | --- |
| `agent.py` | CLI, configuration, atomic report/trace writing and exit codes |
| `tools.py` | `list_files`, `read_file`, `search_code`, `git_log` and dispatch |
| `auditor/agent.py` | Explicit reasoning/tool loop and investigation policy |
| `auditor/provider.py` | HTTPS API client, timeouts and bounded retries |
| `auditor/repository.py` | Temporary bare clone and read-only Git object access |
| `auditor/schema.py` | Tool schemas, report contract, evidence validation and deduplication |
| `auditor/safety.py` | Repository URL validation and redaction |
| `tests/` | Offline unit/integration tests with controlled fixtures and API failure simulation |

## Inspection tools

| Tool | Input and behavior |
| --- | --- |
| `list_files()` | Tracked paths, default 300 per page, maximum 500; follow `next_offset` |
| `read_file(path)` | Redacted numbered UTF-8 source, default lines 1–160; max 200 lines and 16,000 characters per call; follow `next_line` |
| `search_code(pattern)` | Case-insensitive literal search, optional glob; scans at most 200 files per page, returns at most 100 matches |
| `git_log()` | Up to 50 recent commit subjects; default 20, no author emails |

Search offsets refer to files. When the match limit is hit, read the last matching file for its remaining matches, then advance the returned offset by one. Files over 256 KB, binary files, non-UTF-8 files, symlinks and submodules are explicitly unavailable; absence of a search match does not establish absence in skipped files. Large tree metadata over 20 MB causes a fatal error. The clone includes at most 100 commits from the default branch.

## Coverage and false-positive policy

The model investigates repository hygiene, dependency management, code quality, tests, CI, security, configuration, collaboration, and build/release practices. It traces CI triggers, job conditions, permission scopes, checkouts, shell commands, referenced scripts and failure propagation.

An absent optional file is not automatically a violation. The model must consider equivalent controls such as external CI, tox/nox, setup.cfg, another package manager, or documented library packaging practices. Placeholder credentials and fixture examples should not be labeled real leaks. A redaction marker alone is not proof of a credential leak. No remote branch-protection claims, live credential validation, dependency vulnerability claims, or test-pass/fail claims are made without the necessary evidence. Test execution is intentionally unavailable because target code is untrusted.

Each line-based finding must quote a nonempty exact excerpt from an observed redacted line. File-level findings require a discovered path; repository-level absence findings require complete file listing. This enforces provenance, but does not prove semantic correctness: an LLM can still misinterpret evidence. Findings sharing the same rule ID, path and line are deduplicated; the model is also asked to consolidate root causes.

## Output contract

The top-level object contains only `repository` and `violations`. Each finding contains exactly:

| Field | Meaning |
| --- | --- |
| `rule_id` | Stable descriptive identifier, e.g. `CI-IGNORED-FAILURE` |
| `category` | `repository hygiene`, `dependency management`, `code quality`, `testing`, `ci`, `security`, `configuration`, `collaboration`, or `build and release` |
| `severity` | `critical`, `high`, `medium`, or `low` |
| `file` | Tracked relative path, or JSON `null` when inapplicable |
| `line` | Positive one-based line number, or JSON `null` when inapplicable |
| `description` | Concrete defect and its impact |
| `evidence` | Redacted source excerpt or supported structural evidence |
| `recommendation` | Actionable remediation |

Severity reflects demonstrated impact: critical privileged compromise/exposure; high security or validation bypass; medium reliability gaps; low limited maintenance risk. A completed healthy audit uses `"violations": []`. Secret values must never be reproduced.

## Security boundaries and limitations

- Target repositories are never checked out, modified, imported, installed, built or executed. All inspection reads Git objects from a temporary bare clone that is removed afterward.
- Only canonical public HTTPS GitHub repository URLs are accepted. Git credential prompts/helpers, user/system configuration, redirects and non-HTTPS protocols are disabled. Submodules and symlinks are not followed.
- The model cannot invoke a shell or arbitrary tools. Repository content and commit text are explicitly treated as untrusted data; prompt injection can still affect model judgment, but cannot grant executable capabilities.
- Common credential formats, sensitive assignments, private keys, URL passwords and known environment secrets are redacted before transmission. Heuristic redaction cannot guarantee recognition of every custom or encoded secret. Audit only public repositories you are comfortable sharing with your selected provider. Traces contain repository excerpts and must also be reviewed before publishing.
- History is shallow. Remote settings, workflow runs and external service configurations are not inspected. Generated files and files outside the current default branch may be missed.
- Time, response, file and context limits bound ordinary runs. Clone disk usage is not quota-enforced; for hostile or very large targets, run the auditor within a container/VM with CPU, memory, disk and network limits.
- The agent fails explicitly on budget exhaustion, malformed provider responses or unavailable required services. It never fabricates a completed report as a fallback.

## Automated validation

```sh
python -m unittest discover -s tests -v
python -m pip install ruff==0.11.13 build==1.2.2.post1
python -m ruff check .
python -m build
```

Tests cover pagination, literal search, redaction, path/binary/symlink protection, size limits, genuine Git object reads, tool-loop feedback, evidence rejection and repair, malformed responses, budget exhaustion, deduplication, healthy-repository output, atomic writes and missing credentials. Tests use a scripted provider to remain offline and reproducible. They validate program behavior, not real-model detection precision or recall. The CI workflow runs tests, lint and package builds on Linux and Windows with Python 3.11 and 3.13, with read-only repository permissions and no API credentials.

For a live evaluation, use separate public repositories containing (1) obvious seeded issues, (2) subtle CI/security issues, and (3) healthy alternative configurations. Create a manually verified answer set before running. Count unique correct findings and calculate precision = true positives / reported findings and recall = true positives / known defects; report undefined ratios as N/A. Do not claim hidden-evaluation scores or assume the offline tests establish these metrics.

## Authentic sample report

After setting credentials, run a real audit and preserve the tool trace:

```sh
python agent.py --repo https://github.com/pallets/flask --output sample_report.json --trace sample_trace.json
```

Review the generated findings for unsupported claims and secrets before committing. Do not hand-edit output to invent better findings. If correction is needed, improve the agent, rerun, and retain the authentic output. `sample_trace.json` records repository commit, model, completion status and tool calls so the example is reproducible against the recorded snapshot. A future default-branch audit may differ. Until this run succeeds, the assignment's authentic-sample requirement remains outstanding.

## Troubleshooting

| Error | Resolution |
| --- | --- |
| Missing API key | Export `OPENAI_API_KEY` in the current shell; `.env` is not auto-loaded |
| HTTP 401/403 | Verify provider credentials and model access |
| HTTP 429 | Check quota/rate limits, wait and rerun |
| Invalid provider response | Choose a model/endpoint supporting Chat Completions function tools |
| Git operation failed | Verify the URL is public, Git is installed and HTTPS access works |
| Budget/context exhausted | Increase `--max-steps` within limits or investigate a smaller repository; no completed report is fabricated |
| File skipped | Use another source of evidence; the agent must not infer the skipped file's contents |

## Publication and submission

Once tests and the authentic sample pass, create a public GitHub repository and push this project's files. With GitHub CLI installed and authenticated:

```sh
git init -b main
git add .
git commit -m "Implement LLM-directed DevOps repository auditing agent"
gh repo create devops-repository-auditing-agent --public --source . --remote origin --push
```

Confirm the repository is publicly accessible, CI passes, and `agent.py`, `tools.py`, `auditor/`, `requirements.txt`, `.env.example`, `README.md`, `tests/` and the authentic `sample_report.json` are present. Keep all setup and explanatory material in this README. Submit **only the public GitHub repository URL** through the course submission channel. Do not submit a ZIP, screenshots, or a handwritten report.

## Portfolio presentation

Demonstrate a live run, show a follow-up tool call in the trace, explain a verified finding and a rejected false positive, and discuss the safety and evidence boundaries. A truthful CV description after completing the live validation is: “Built a Python LLM-directed DevOps auditing agent with read-only Git inspection, evidence-grounded JSON reports, bounded tool execution, and automated cross-platform validation.” Add measured accuracy or performance only after running and documenting an actual evaluation.

## References

- [OpenAI Chat Completions function-tool API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Git cat-file documentation](https://git-scm.com/docs/git-cat-file)
- [Git ls-tree documentation](https://git-scm.com/docs/git-ls-tree)

## Review guidance

For changes, open a pull request explaining the defect, resulting behavior and validation. Review tool allowlists, untrusted-input boundaries, redaction, evidence grounding and failure handling. Add regression tests for new behaviors, never add real secrets to fixtures, and preserve the required CLI and JSON contract.
