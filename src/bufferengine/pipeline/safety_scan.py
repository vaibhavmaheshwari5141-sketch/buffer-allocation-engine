"""Scan the working tree and git history for secrets and personal data before a repo is made public.

Looks for: access tokens and keys, password / secret / token assignments, credential file names (.env,
.databrickscfg), e-mail addresses, and absolute local paths that contain a user name (C:\\Users\\..., /Users/..., /home/...).
Findings are printed with masked values. History findings are only reported; history is never rewritten.

Usage:
    python -m bufferengine.pipeline.safety_scan [--allow-email you@example.org ...] [--write-report docs/d2/safety_scan.md]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

PATTERNS = {
    "github_token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|github_pat_[A-Za-z0-9_]{30,}"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "databricks_token": re.compile(r"\bdapi[a-f0-9]{30,}\b"),
    "jwt_like": re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\."),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "secret_assignment": re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"),
    "databricks_host_with_token": re.compile(r"(?i)DATABRICKS_(TOKEN|HOST)\s*[:=]\s*\S+"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "windows_user_path": re.compile(r"[A-Za-z]:(\\\\|\\|/)Users(\\\\|\\|/)[^\\/\s\"'<>|]+"),
    "unix_user_path": re.compile(r"(?<![A-Za-z0-9_.-])/(Users|home)/[A-Za-z0-9._-]+"),
    "encoded_user_path": re.compile(r"(?i)C%3A/Users/|C:/Users/[^/\s]+/OneDrive"),
}
BAD_FILENAMES = re.compile(r"(^|/)(\.env(\..*)?|\.databrickscfg|id_rsa|id_ed25519|credentials(\.json)?|.*\.pem|.*\.pfx|.*\.p12)$")
# Placeholders and documentation examples that are not real findings.
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "users.noreply.github.com", "noreply.anthropic.com", "anthropic.com"}
BINARY_SUFFIXES = {".parquet", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".ico", ".xlsx", ".pyc", ".whl"}


def mask(kind: str, text: str) -> str:
    if kind == "email":
        user, _, dom = text.partition("@")
        return f"{user[:1]}***@{dom}"
    if kind.endswith("path"):
        return re.sub(r"(Users|home)(\\\\|\\|/)[^\\/]+", r"\1\2<user>", text)
    return text[:6] + "..." if len(text) > 8 else "***"


def scan_text(name: str, text: str, allow_emails: set[str]) -> list[tuple[str, str, int, str]]:
    out = []
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(text):
            val = m.group(0)
            if kind == "email" and (val.lower() in allow_emails or val.split("@")[-1].lower() in SAFE_EMAIL_DOMAINS):
                continue
            line = text.count("\n", 0, m.start()) + 1
            out.append((kind, name, line, mask(kind, val)))
    return out


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, check=True).stdout.decode("utf-8", errors="replace")


def working_tree(allow_emails: set[str]) -> list[tuple[str, str, int, str]]:
    files = run(["git", "ls-files", "-z"]).split("\0") + run(["git", "ls-files", "-o", "--exclude-standard", "-z"]).split("\0")
    findings = []
    for rel in sorted({f for f in files if f}):
        p = REPO / rel
        if rel.endswith("pipeline/safety_scan.py"):
            continue  # contains the patterns it searches for
        if BAD_FILENAMES.search(rel):
            findings.append(("credential_filename", rel, 0, rel))
        if not p.is_file() or p.suffix.lower() in BINARY_SUFFIXES or p.stat().st_size > 20_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings += scan_text(rel, text, allow_emails)
    return findings


def history(allow_emails: set[str]) -> tuple[list[tuple[str, str, int, str, str]], dict[str, int]]:
    """Scan every blob reachable from any commit once; report the first commit where it appears."""
    out, seen = [], set()
    commits = run(["git", "rev-list", "--all"]).split()
    authors: dict[str, int] = defaultdict(int)
    for line in run(["git", "log", "--all", "--format=%ae|%ce"]).splitlines():
        for e in set(line.split("|")):
            authors[e] += 1
    for c in reversed(commits):
        tree = run(["git", "ls-tree", "-r", "-z", c]).split("\0")
        for entry in tree:
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            blob = meta.split()[2]
            if blob in seen:
                continue
            seen.add(blob)
            if Path(path).suffix.lower() in BINARY_SUFFIXES or path.endswith("pipeline/safety_scan.py"):
                continue
            if BAD_FILENAMES.search(path):
                out.append(("credential_filename", path, 0, path, c[:8]))
            data = subprocess.run(["git", "cat-file", "-p", blob], cwd=REPO, capture_output=True).stdout
            if len(data) > 20_000_000:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            out += [(*f, c[:8]) for f in scan_text(path, text, allow_emails)]
    return out, dict(authors)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-email", action="append", default=[], help="team e-mail addresses that may appear")
    ap.add_argument("--write-report", help="write a masked Markdown report to this path (relative to the repo)")
    a = ap.parse_args()
    allow = {e.lower() for e in a.allow_email}
    wt = working_tree(allow)
    hist, authors = history(allow)

    def summarise(rows, with_commit):
        agg: dict[tuple, int] = defaultdict(int)
        for r in rows:
            agg[(r[0], r[1]) + ((r[4],) if with_commit else ())] += 1
        return agg

    print(f"WORKING TREE: {len(wt)} findings")
    for (kind, f), n in sorted(summarise(wt, False).items()):
        print(f"  {kind:22s} {f}  x{n}")
    print(f"HISTORY: {len(hist)} findings in blobs reachable from any commit")
    for (kind, f, c), n in sorted(summarise(hist, True).items()):
        print(f"  {kind:22s} {f}  (first seen {c})  x{n}")
    print("COMMIT AUTHOR / COMMITTER ADDRESSES (masked):")
    for e, n in authors.items():
        flag = "" if e.lower() in allow or e.split("@")[-1].lower() in SAFE_EMAIL_DOMAINS else "  <-- not on the allow list"
        print(f"  {mask('email', e)}  in {n} commit(s){flag}")
    if a.write_report:
        lines = ["# Public-repo safety scan", "", "Generated by `python -m bufferengine.pipeline.safety_scan`. Values are masked.", "",
                 f"- Working-tree findings: **{len(wt)}**", f"- Findings in git history (blobs reachable from any commit): **{len(hist)}**", ""]
        lines += ["## Working tree", ""] + ([f"- {k}: `{f}` x{n}" for (k, f), n in sorted(summarise(wt, False).items())] or ["None."])
        lines += ["", "## History", ""] + ([f"- {k}: `{f}` first seen in {c} x{n}" for (k, f, c), n in sorted(summarise(hist, True).items())] or ["None."])
        (REPO / a.write_report).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
