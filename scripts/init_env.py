"""Create or check the local .env (standard library only - no virtualenv needed).

    python scripts/init_env.py            # create .env from .env.example with generated secrets
    python scripts/init_env.py --check    # verify .env: every secret present, no placeholders
    python scripts/init_env.py --add-missing   # append secrets added to .env.example later

Generated values never leave this machine and are never printed. Production should not use a
.env file at all: inject the same variables from a secret manager (see SECURITY.md).
"""

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE, TARGET = ROOT / ".env.example", ROOT / ".env"
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
# URL variables embed another variable's secret instead of their own.
URL_SECRET = {"DATABASE_URL": "POSTGRES_PASSWORD", "REDIS_URL": "REDIS_PASSWORD"}


def parse(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def generate() -> int:
    if TARGET.exists():
        print(".env already exists - not overwritten. Use --check to validate it.")
        return 1
    values: dict[str, str] = {}
    lines = []
    for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw.strip())
        if m and "CHANGE_ME" in m.group(2) and m.group(1) not in URL_SECRET:
            values[m.group(1)] = secrets.token_urlsafe(48)  # 64 chars of [A-Za-z0-9_-]
            raw = f"{m.group(1)}={values[m.group(1)]}"
        lines.append(raw)
    for i, raw in enumerate(lines):
        m = LINE.match(raw.strip())
        if m and m.group(1) in URL_SECRET:
            lines[i] = raw.replace("CHANGE_ME", values[URL_SECRET[m.group(1)]])
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created .env with {len(values)} generated secrets (values not shown).")
    return check()


def check() -> int:
    if not TARGET.exists():
        print("FAIL: .env missing - run: python scripts/init_env.py")
        return 1
    want, have = parse(EXAMPLE), parse(TARGET)
    required = [k for k, v in want.items() if "CHANGE_ME" in v]  # secrets: no safe default
    problems = [f"missing {k}" for k in required if k not in have]
    optional = [k for k in want if k not in have and k not in required]
    problems += [f"{k} still a placeholder" for k, v in have.items() if "CHANGE_ME" in v]
    for key in ("JWT_SECRET", "ENTITY_HASH_KEY", "FINGERPRINT_HMAC_KEY"):
        if key in have and len(have[key]) < 32:
            problems.append(f"{key} shorter than 32 characters")
    keys = [have.get(k) for k in ("JWT_SECRET", "ENTITY_HASH_KEY", "FINGERPRINT_HMAC_KEY")]
    if None not in keys and len(set(keys)) != len(keys):
        problems.append("JWT_SECRET, ENTITY_HASH_KEY and FINGERPRINT_HMAC_KEY must differ")
    for key in [k for k in have if k.endswith("_DB_PASSWORD")]:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", have[key]):
            problems.append(f"{key} must be 16-128 chars of [A-Za-z0-9_-]")
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print(f"OK: all {len(required)} secrets set, no placeholders.", end=" ")
    print(f"{len(optional)} optional settings use their defaults." if optional else "")
    return 0


def add_missing() -> int:
    """Append secrets that .env.example gained after .env was created (values not printed)."""
    want, have = parse(EXAMPLE), parse(TARGET)
    new = [k for k, v in want.items() if "CHANGE_ME" in v and k not in have and k not in URL_SECRET]
    if new:
        with TARGET.open("a", encoding="utf-8") as fh:
            fh.write("\n# added by scripts/init_env.py --add-missing\n")
            for k in new:
                fh.write(f"{k}={secrets.token_urlsafe(48)}\n")
    print(f"Added {len(new)} secret(s): {', '.join(new) or '-'}")
    return check()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--add-missing", action="store_true")
    a = ap.parse_args()
    sys.exit(check() if a.check else add_missing() if a.add_missing else generate())
