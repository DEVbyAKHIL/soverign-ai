"""The production image copies an explicit list of directories; every first-party package the
app imports must be on it (a missing COPY only shows up as a crash when the container starts)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRST_PARTY = {p.name for p in ROOT.iterdir() if (p / "__init__.py").exists()} - {"tests", "ml"}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return {m for m in out if m.split(".")[0] in FIRST_PARTY}


def _module_file(module: str) -> Path | None:
    base = ROOT.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _closure(roots: set[str]) -> set[Path]:
    """Every first-party file reachable by imports from the given top-level packages."""
    todo = [f for r in roots for f in (ROOT / r).rglob("*.py")]
    seen: set[Path] = set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for module in _imports(f):
            parts = module.split(".")
            for i in range(1, len(parts) + 1):  # package __init__ files run on import too
                target = _module_file(".".join(parts[:i]))
                if target is not None and target not in seen:
                    todo.append(target)
    return seen


def _copied(dockerfile: str) -> tuple[set[str], set[str]]:
    """(whole directories, individual files) the Dockerfile copies into /app."""
    text = (ROOT / "infra" / "docker" / dockerfile).read_text(encoding="utf-8")
    dirs, files = set(), set()
    for line in re.findall(r"^COPY --chown=\S+ (.+) \./\S*$", text, re.MULTILINE):
        for src in line.split():
            (files if src.endswith(".py") else dirs).add(src)
    return dirs, files


def _missing(dockerfile: str, roots: set[str]) -> list[str]:
    dirs, files = _copied(dockerfile)
    out = []
    for f in _closure(roots):
        rel = f.relative_to(ROOT).as_posix()
        if rel.split("/")[0] not in dirs and rel not in files:
            out.append(rel)
    return sorted(out)


def test_runtime_image_copies_every_imported_first_party_package():
    roots = {"gateway"}
    assert not _missing("gateway.Dockerfile", roots)


def test_streaming_image_copies_every_imported_first_party_module():
    assert not _missing("streaming.Dockerfile", {"producer", "publisher", "events"})


def test_schema_files_are_shipped_with_the_event_contract():
    for dockerfile in ("gateway.Dockerfile", "streaming.Dockerfile"):
        assert "schemas" in _copied(dockerfile)[0], dockerfile


def test_runtime_code_does_not_import_training_code():
    # ml/ (training, synthetic data, scikit-learn) is not shipped in the runtime image.
    offenders = []
    for pkg in FIRST_PARTY:
        for path in (ROOT / pkg).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import) ml[\s.]", source, re.MULTILINE):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders
