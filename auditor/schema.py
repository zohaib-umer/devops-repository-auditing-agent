"""Tool contracts and evidence-aware report validation."""

FIELDS = {"rule_id", "category", "severity", "file", "line", "description", "evidence", "recommendation"}
CATEGORIES = ["repository hygiene", "dependency management", "code quality", "testing",
              "ci", "security", "configuration", "collaboration", "build and release"]


def tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(required), "additionalProperties": False}}}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
VIOLATION = {"type": "object", "additionalProperties": False, "required": sorted(FIELDS),
             "properties": {**{k: STRING for k in FIELDS},
                            "category": {"type": "string", "enum": CATEGORIES},
                            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                            "file": {"type": ["string", "null"]},
                            "line": {"type": ["integer", "null"]}}}
TOOLS = [
    tool("list_files", "List tracked files with pagination; use next_offset to continue.",
         {"offset": INTEGER, "limit": INTEGER}),
    tool("read_file", "Read numbered, redacted lines; at most 200 lines per call. Follow next_line.",
         {"path": STRING, "start_line": INTEGER, "end_line": INTEGER}, ["path"]),
    tool("search_code", "Case-insensitive LITERAL search, optional file glob. offset paginates files, not matches.",
         {"pattern": STRING, "glob": STRING, "offset": INTEGER, "limit": INTEGER}, ["pattern"]),
    tool("git_log", "Read recent commit subjects; limited shallow history, no identity data.", {"limit": INTEGER}),
    tool("finish_audit", "Submit only verified violations. For line findings evidence must be an exact nonempty substring of observed redacted source on that line. Empty violations is valid.",
         {"violations": {"type": "array", "items": VIOLATION}}, ["violations"]),
]


def validate_report(payload, repository, inspections):
    if not isinstance(payload, dict) or set(payload) != {"violations"} or not isinstance(payload["violations"], list):
        raise ValueError("Submit an object with only a violations array")
    if len(payload["violations"]) > 100:
        raise ValueError("At most 100 findings are allowed")
    findings, seen = [], set()
    for item in payload["violations"]:
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise ValueError("Each violation must have exactly the required fields")
        for key in FIELDS - {"file", "line"}:
            if not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 4000:
                raise ValueError(f"Invalid {key}")
        if item["category"] not in CATEGORIES or item["severity"] not in {"critical", "high", "medium", "low"}:
            raise ValueError("Invalid category or severity")
        path, line = item["file"], item["line"]
        if path is None:
            if line is not None:
                raise ValueError("A line requires a file")
            if len(inspections.listed) != len(inspections.entries):
                raise ValueError("Repository-level absence claims require complete file discovery")
        else:
            if not isinstance(path, str) or path not in inspections.entries:
                raise ValueError("Finding file must exist in the inspected commit; use null for absence")
            if line is None:
                if path not in inspections.listed:
                    raise ValueError("File-level findings must cite a discovered file")
            elif type(line) is not int or line < 1:
                raise ValueError("line must be a positive integer or null")
            else:
                observed = inspections.observed.get((path, line))
                if observed is None or item["evidence"] not in observed:
                    raise ValueError("Line evidence must quote observed, redacted text at the reported line")
        identity = (item["rule_id"], path, line)
        if identity not in seen:
            seen.add(identity)
            findings.append(item)
    return {"repository": repository, "violations": findings}
