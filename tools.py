"""Bounded, read-only inspection tools over a fixed Git commit."""

import fnmatch

from auditor.safety import Redactor

MAX_FILE_BYTES = 256_000


class InspectionTools:
    def __init__(self, repository, redactor=None):
        self.repo = repository
        self.redactor = redactor or Redactor()
        self.entries = {}
        raw = repository.git("ls-tree", "-rz", "--full-tree", "HEAD")
        if len(raw) > 20_000_000:
            raise RuntimeError("Repository tree exceeds the supported 20 MB metadata limit")
        for record in raw.split(b"\0"):
            if record:
                meta, path = record.split(b"\t", 1)
                mode, kind, oid = meta.decode().split()
                self.entries[path.decode("utf-8", errors="replace")] = (mode, kind, oid)
        self.observed = {}
        self.listed = set()
        self.cache = {}

    def list_files(self, offset=0, limit=300):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("offset must be nonnegative and limit must be 1..500")
        paths = sorted(self.entries)
        selected = paths[offset:offset + limit]
        self.listed.update(selected)
        return {"files": selected, "total": len(paths),
                "next_offset": offset + limit if offset + limit < len(paths) else None}

    def _lines(self, path):
        if not isinstance(path, str) or path not in self.entries:
            raise ValueError("Path is not a tracked file in this commit")
        mode, kind, oid = self.entries[path]
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Symlinks and submodules are not followed")
        if path not in self.cache:
            size = int(self.repo.git("cat-file", "-s", oid))
            if size > MAX_FILE_BYTES:
                raise ValueError("File exceeds 256000 bytes; inspect another source of evidence")
            data = self.repo.git("cat-file", "blob", oid)
            if b"\0" in data:
                raise ValueError("Binary file cannot be inspected as text")
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("File is not UTF-8 text") from None
            self.cache[path] = self.redactor.text(text).splitlines()
        return self.cache[path]

    def read_file(self, path, start_line=1, end_line=160):
        if (type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or end_line < start_line or end_line - start_line >= 200):
            raise ValueError("Use a positive line range of at most 200 lines")
        lines = self._lines(path)
        result, chars = [], 0
        for number in range(start_line, min(end_line, len(lines)) + 1):
            content = lines[number - 1][:2000]
            if chars + len(content) > 16000:
                break
            chars += len(content)
            self.observed[(path, number)] = content
            result.append({"line": number, "text": content,
                           "truncated": len(lines[number - 1]) > 2000})
        last = result[-1]["line"] if result else start_line - 1
        return {"path": path, "lines": result, "total_lines": len(lines),
                "next_line": last + 1 if last < len(lines) else None}

    def search_code(self, pattern, glob="*", offset=0, limit=50):
        # Literal search avoids catastrophic regex backtracking on hostile files.
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 200:
            raise ValueError("pattern must be a literal string of 1..200 characters")
        if not isinstance(glob, str) or len(glob) > 200:
            raise ValueError("glob must be a short file pattern")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative and limit must be 1..100")
        candidates = [p for p in sorted(self.entries) if fnmatch.fnmatchcase(p, glob)]
        matches, skipped = [], []
        selected = candidates[offset:offset + 200]
        next_offset = None
        for index, path in enumerate(selected, offset):
            try:
                lines = self._lines(path)
            except ValueError as exc:
                skipped.append({"path": path, "reason": str(exc)})
                continue
            for number, text in enumerate(lines, 1):
                if pattern.casefold() in text.casefold():
                    excerpt = text[:1000]
                    self.observed[(path, number)] = excerpt
                    matches.append({"file": path, "line": number, "text": excerpt})
                    if len(matches) >= limit:
                        return {"matches": matches, "skipped": skipped,
                                "next_offset": index, "truncated": True,
                                "note": "Read the last matching file for remaining matches; then advance offset by one"}
            next_offset = index + 1
        return {"matches": matches, "skipped": skipped,
                "next_offset": next_offset if next_offset and next_offset < len(candidates) else None,
                "truncated": offset + len(selected) < len(candidates)}

    def git_log(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        text = self.repo.git("log", f"-{limit}", "--format=%h %cs %s", "--no-show-signature").decode("utf-8", "replace")
        return {"history": self.redactor.text(text[:12000]), "shallow": True,
                "note": "At most 100 commits cloned; absence is not evidence about full history"}

    def dispatch(self, name, arguments):
        allowed = {"list_files", "read_file", "search_code", "git_log"}
        if name not in allowed:
            return {"error": "Unknown inspection tool"}
        try:
            return self.redactor.clean(getattr(self, name)(**arguments))
        except (TypeError, ValueError, RuntimeError) as exc:
            return {"error": self.redactor.text(str(exc))}
