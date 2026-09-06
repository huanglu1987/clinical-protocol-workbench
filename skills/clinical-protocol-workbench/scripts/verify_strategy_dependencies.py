"""Read-only integrity check for a selected dermatology strategy dependency."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


LOCK_PATH = Path(__file__).resolve().parents[1] / "assets/strategy-dependencies.lock.json"
CONTROLLED_DIRECTORIES = {"references", "agents", "assets", "scripts"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def controlled(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return relative == "SKILL.md" or parts[0] in CONTROLLED_DIRECTORIES


def verify_module(lock: dict, module: str, root: Path) -> dict:
    if lock.get("schema_version") != 1:
        raise ValueError("Unsupported dependency lock schema")
    if module not in lock.get("modules", {}):
        raise ValueError("Module is not in the dependency lock")
    spec = lock["modules"][module]
    expected = spec.get("files", {})
    if not expected or "SKILL.md" not in expected:
        raise ValueError("Lock must include SKILL.md")
    for relative, digest in expected.items():
        path = PurePosixPath(relative)
        if (
            not relative
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or path.as_posix() != relative
            or not controlled(relative)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("Invalid path or digest in dependency lock")

    root = root.expanduser().resolve()
    errors: list[dict[str, str]] = []
    actual: dict[str, str] = {}
    if not root.is_dir():
        errors.append({"kind": "missing_root", "path": str(root)})
    else:
        for relative, expected_hash in expected.items():
            target = root / relative
            components = [root.joinpath(*PurePosixPath(relative).parts[:i])
                          for i in range(1, len(PurePosixPath(relative).parts) + 1)]
            if any(part.is_symlink() for part in components):
                errors.append({"kind": "symlink", "path": relative})
                continue
            if not target.is_file():
                errors.append({"kind": "missing_file", "path": relative})
                continue
            try:
                actual[relative] = sha256(target.read_bytes())
            except OSError:
                errors.append({"kind": "unreadable", "path": relative})
                continue
            if actual[relative] != expected_hash:
                errors.append({"kind": "hash_mismatch", "path": relative})
        for target in sorted(root.rglob("*")):
            relative = target.relative_to(root).as_posix()
            if target.name == ".DS_Store" or "__pycache__" in target.parts:
                continue
            if controlled(relative) and relative not in expected:
                if target.is_file() or target.is_symlink():
                    errors.append({"kind": "unlocked_file", "path": relative})

    status = "matched"
    if errors:
        status = "missing" if all(e["kind"].startswith("missing_") for e in errors) else "mismatch"
    return {
        "module": module,
        "repository": spec["repository"],
        "commit": spec["commit"],
        "root": str(root),
        "status": status,
        "expected_file_count": len(expected),
        "checked_file_count": len(actual),
        "errors": errors,
        "actual_sha256": actual,
        "not_claimed": ["clinical validation", "human approval", "runtime read isolation"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        lock_bytes = LOCK_PATH.read_bytes()
        report = verify_module(json.loads(lock_bytes), args.module, args.root)
        report["lock_sha256"] = sha256(lock_bytes)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
