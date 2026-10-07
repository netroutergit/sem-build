#!/usr/bin/env python3
"""Gate for commits: fail if anything in the repo looks like it should not be public.

Usage:
    python tools/check_clean.py            # scan the whole repo
    python tools/check_clean.py --staged   # scan only files staged in git
    python tools/check_clean.py --root DIR # scan some other folder

Checks:
  * images: any EXIF block, XMP block or GPS tag fails. Raw and HEIC
    originals fail by extension (they always carry metadata).
  * text files: email addresses, phone numbers and secret-looking strings
    (API keys, tokens, private keys, password= lines) fail.
  * text files: dollar amounts are reported as warnings only, because
    prices paid stay out of this repo as a house rule. Warnings do not
    change the exit code.

  * text files: any word listed in the local banned-word file fails.
    The file is .git/info/banned-words.txt (one word or phrase per line,
    case-insensitive, lines starting with # ignored). It is local to this
    clone and never published, so the list itself stays private.
    --banned-words FILE points at a different list; --check-message FILE
    runs only the banned-word check on a commit message.

Skips .git/, intake/ and the usual editor and Python junk. Exit code is
1 when anything fails, 0 otherwise, so it can gate a commit.

A line that is a known false positive can be marked with the comment
"check-clean: allow" and will be skipped.
"""

import argparse
import os
import re
import subprocess
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

SKIP_DIRS = {".git", "intake", "__pycache__", ".venv", "venv", "node_modules",
             ".idea", ".vscode"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
BANNED_EXT = {".heic", ".heif", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng"}
BINARY_EXT = {".glb", ".gltf", ".bin", ".zip", ".pdf", ".woff", ".woff2",
              ".ttf", ".otf", ".ico", ".mp4", ".mov"}
MAX_TEXT_BYTES = 20 * 1024 * 1024
ALLOW_MARK = "check-clean: allow"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# North American numbers must have a bracketed area code or dash/dot
# separators, so whitespace-separated number columns (mesh files, CSV)
# and STEP coordinates do not trip it. International needs a leading +.
PHONE = re.compile(
    r"(?<![\w.])(?:\+?1[ .-]?)?(?:\(\d{3}\) ?|\d{3}[.-])\d{3}[ .-]\d{4}(?!\w|\.\d)"
    r"|(?<![\w.])\+\d{1,3}[ .-]?\(?\d{1,4}\)?[ .-]\d{2,4}[ .-]\d{3,4}(?:[ .-]\d{2,4})?(?!\w|\.\d)"
)

SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GitHub PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\b")),
    ("credential assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|secret|access[_-]?token|auth[_-]?token"
        r"|token|password|passwd|pwd|client[_-]?secret)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=.]{8,}")),
    ("URL with embedded password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
]

DOLLARS = re.compile(r"(?<![A-Za-z])\$\s?\d[\d,]*(?:\.\d+)?")

Finding = tuple[str, str, int, str]  # level, path, line, message


def load_banned(path: str | None) -> list[str]:
    """Read the local banned-word list. Missing file means an empty list."""
    if not path or not os.path.isfile(path):
        return []
    words = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line.lower())
    return words


def banned_hits(text: str, banned: list[str]) -> list[str]:
    """Banned words that occur in text as whole words (case-insensitive)."""
    low = text.lower()
    return [w for w in banned
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)]


def check_message(path: str, banned: list[str]) -> list[Finding]:
    """Banned-word check on a commit message file (for a commit-msg hook)."""
    out: list[Finding] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if line.startswith("#"):
                continue  # git's own comment lines
            for w in banned_hits(line, banned):
                out.append(("FAIL", "commit message", n, f"banned word: {w}"))
    return out


def git_staged(root: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=root, capture_output=True, check=True).stdout
    names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
    return [os.path.join(root, n.replace("/", os.sep)) for n in names]


def walk(root: str) -> list[str]:
    """Every file git would publish: tracked plus untracked-but-not-ignored.

    Falls back to a plain directory walk when root is not a git repo.
    """
    if os.path.isdir(os.path.join(root, ".git")):
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, check=True).stdout
        names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
        return [os.path.join(root, n.replace("/", os.sep)) for n in names]
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            paths.append(os.path.join(dirpath, f))
    return paths


def rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def check_image(path: str, display: str) -> list[Finding]:
    out: list[Finding] = []
    if Image is None:
        return [("FAIL", display, 0, "Pillow not installed; cannot inspect image")]
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if exif:
                msg = f"EXIF present ({len(exif)} tags"
                if 0x8825 in exif or exif.get_ifd(0x8825):
                    msg += ", including GPS"
                out.append(("FAIL", display, 0, msg + ")"))
            if im.info.get("xmp"):
                out.append(("FAIL", display, 0, "XMP metadata present"))
            if im.info.get("photoshop"):
                out.append(("FAIL", display, 0, "Photoshop/IPTC block present"))
            if im.info.get("comment"):
                out.append(("FAIL", display, 0, "comment block present"))
    except Exception as e:  # unreadable image is suspicious enough to fail
        out.append(("FAIL", display, 0, f"could not open image: {e}"))
    return out


def check_text(path: str, display: str, banned: list[str] = ()) -> list[Finding]:
    out: list[Finding] = []
    try:
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            return [("WARN", display, 0, "file too large to scan")]
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return [("FAIL", display, 0, f"could not read: {e}")]
    if b"\0" in raw[:8192]:
        return []  # binary, not text
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for m in EMAIL.finditer(line):
            out.append(("FAIL", display, n, f"email address: {m.group(0)}"))
        for m in PHONE.finditer(line):
            out.append(("FAIL", display, n, f"phone number: {m.group(0).strip()}"))
        for name, rx in SECRETS:
            for m in rx.finditer(line):
                snippet = m.group(0)
                if len(snippet) > 40:
                    snippet = snippet[:24] + "..." + snippet[-8:]
                out.append(("FAIL", display, n, f"{name}: {snippet}"))
        for m in DOLLARS.finditer(line):
            out.append(("WARN", display, n, f"dollar amount: {m.group(0)}"))
        for w in banned_hits(line, banned):
            out.append(("FAIL", display, n, f"banned word: {w}"))
    return out


def scan(root: str, paths: list[str], banned: list[str] = ()) -> list[Finding]:
    self_path = os.path.abspath(__file__)
    findings: list[Finding] = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        parts = os.path.normpath(os.path.relpath(p, root)).split(os.sep)
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        display = rel(root, p)
        name = os.path.basename(p)
        ext = os.path.splitext(name)[1].lower()
        stem = os.path.splitext(name)[0].lower()

        if ext in BANNED_EXT or stem.endswith(("-original", "_original")):
            findings.append(("FAIL", display, 0,
                             "original or raw photo format; run tools/clean_photo.py instead"))
            continue
        if ext in IMAGE_EXT:
            findings.extend(check_image(p, display))
            continue
        if ext in BINARY_EXT:
            continue
        if os.path.abspath(p) == self_path:
            continue  # the detector's own regexes would trip the detector
        findings.extend(check_text(p, display, banned))
    # Banned words apply to file names too, not just contents.
    for p in paths:
        if os.path.isfile(p):
            for w in banned_hits(rel(root, p), banned):
                findings.append(("FAIL", rel(root, p), 0, f"banned word in file name: {w}"))
    return findings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   help="folder to scan (default: the repo this script lives in)")
    p.add_argument("--staged", action="store_true",
                   help="scan only files staged in git, for use as a pre-commit hook")
    p.add_argument("-q", "--quiet", action="store_true", help="print failures only")
    p.add_argument("--banned-words", default=None,
                   help="word list file (default: .git/info/banned-words.txt under --root)")
    p.add_argument("--check-message", default=None, metavar="FILE",
                   help="check only this commit-message file against the banned words")
    a = p.parse_args(argv)

    root = os.path.abspath(a.root)
    banned = load_banned(a.banned_words or os.path.join(root, ".git", "info", "banned-words.txt"))

    if a.check_message:
        findings = check_message(a.check_message, banned)
        for _, where, line, msg in findings:
            print(f"FAIL  {where}:{line}  {msg}")
        if findings:
            print("NOT CLEAN. Reword the commit message.")
            return 1
        return 0

    paths = git_staged(root) if a.staged else walk(root)
    findings = scan(root, paths, banned)

    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    shown = fails if a.quiet else findings
    for level, path, line, msg in shown:
        where = f"{path}:{line}" if line else path
        print(f"{level}  {where}  {msg}")

    n = sum(1 for p_ in paths if os.path.isfile(p_))
    print(f"scanned {n} files: {len(fails)} failures, {len(warns)} warnings")
    if fails:
        print("NOT CLEAN. Fix the items above before committing.")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
