#!/usr/bin/env python3
"""Release maintenance: hash pre-fetched, pinned dependency trees (no network)."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skills/clinical-protocol-workbench"
SPEC = importlib.util.spec_from_file_location("bundle", CORE / "scripts/install_bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

SOURCES = [
    ("oral-derm-clinical-strategy", "huanglu1987/oral-derm-clinical-strategy", "b927d7d98283d489ab2661cd9551d0d4068cda37", "."),
    ("topical-clinical-strategy", "huanglu1987/Topical-Clinical-Strategy-Skill", "7edded14ad8bbb32a17e8ce2590efa735ff73c8c", "topical-clinical-strategy"),
    ("clinical-doc-qc", "huanglu1987/clinical-doc-qc", "90a1cbd52ff1b975f774d3b1280d7b4b71ab322f", "."),
    ("paper-lookup", "K-Dense-AI/claude-scientific-skills", "1e5eeffbdad3749125afe7ab48a39694e27f181c", "skills/paper-lookup"),
    ("database-lookup", "K-Dense-AI/claude-scientific-skills", "1e5eeffbdad3749125afe7ab48a39694e27f181c", "skills/database-lookup"),
    ("citation-management", "K-Dense-AI/claude-scientific-skills", "1e5eeffbdad3749125afe7ab48a39694e27f181c", "skills/citation-management"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, help="Already fetched dependency directories at the pinned commits")
    args = parser.parse_args()
    path = CORE / bundle.LOCK
    if args.sources:
        skills = [{"name": bundle.CORE, "files": bundle.fingerprints(CORE, {bundle.LOCK})}]
        strategy = json.loads((CORE / "assets/strategy-dependencies.lock.json").read_text())
        for name, repo, commit, subpath in SOURCES:
            tree = args.sources / name
            files = bundle.fingerprints(tree)
            if name in strategy["modules"]:
                expected = strategy["modules"][name]["files"]
                if any(files.get(filename) != digest for filename, digest in expected.items()):
                    raise ValueError(f"Strategy fingerprints do not match: {name}")
            skills.append({"name": name, "repo": repo, "commit": commit, "path": subpath, "files": files})
        lock = {"schema_version": 1, "version": "v0.1.0-preview.5", "skills": skills}
    else:
        lock = json.loads(path.read_text())
        next(item for item in lock["skills"] if item["name"] == bundle.CORE)["files"] = bundle.fingerprints(CORE, {bundle.LOCK})
    lock["version"] = "v0.1.0-preview.5"
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Locked {len(lock['skills'])} skills; only release maintainers may refresh this file.")


if __name__ == "__main__":
    main()
