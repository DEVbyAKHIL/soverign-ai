"""Source files must not contain invisible or bidirectional control characters ("Trojan Source",
CVE-2021-42574). Tests that need such characters must build them with escapes or chr()."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RANGES = [(0x00AD, 0x00AD), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2069), (0xFEFF, 0xFEFF)]
INVISIBLE = re.compile("[" + "".join(chr(a) + "-" + chr(b) for a, b in RANGES) + "]")
SKIP = {".venv", "node_modules", ".git", "dist", ".pytest_cache", ".ruff_cache", "__pycache__"}
SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".ini", ".ts", ".tsx", ".json", ".md", ".mako"}


def _files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in SUFFIXES and not (set(p.relative_to(ROOT).parts) & SKIP):
            yield p


@pytest.mark.parametrize("path", list(_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_invisible_or_bidi_characters(path):
    text = path.read_text(encoding="utf-8")
    hits = [(i, hex(ord(c))) for i, c in enumerate(text) if INVISIBLE.match(c)]
    assert not hits, f"{path.relative_to(ROOT)} contains invisible characters at {hits[:5]}"
