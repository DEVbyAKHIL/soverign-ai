"""The production image copies an explicit list of directories; every first-party package the
app imports must be on it (a missing COPY only shows up as a crash when the container starts)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRST_PARTY = {p.name for p in ROOT.iterdir() if (p / "__init__.py").exists()} - {"tests", "ml"}


def _imported_packages() -> set[str]:
    found: set[str] = set()
    for pkg in FIRST_PARTY:
        for path in (ROOT / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    found.add(node.module.split(".")[0])
                elif isinstance(node, ast.Import):
                    found.update(a.name.split(".")[0] for a in node.names)
    return found & FIRST_PARTY


def test_runtime_image_copies_every_imported_first_party_package():
    dockerfile = (ROOT / "infra" / "docker" / "gateway.Dockerfile").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY --chown=\S+ (\w+) \./\w+", dockerfile, re.MULTILINE))
    missing = _imported_packages() - copied
    assert not missing, f"gateway.Dockerfile does not COPY: {sorted(missing)}"


def test_runtime_code_does_not_import_training_code():
    # ml/ (training, synthetic data, scikit-learn) is not shipped in the runtime image.
    offenders = []
    for pkg in FIRST_PARTY:
        for path in (ROOT / pkg).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import) ml[\s.]", source, re.MULTILINE):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, offenders
