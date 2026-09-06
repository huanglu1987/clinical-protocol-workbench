#!/usr/bin/env python3
"""Package only a frozen Git revision, including two levels of checksums."""
import argparse
import hashlib
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "--verify", args.ref + "^{commit}"], cwd=root, text=True).strip()
    if args.out.exists():
        raise SystemExit("Output already exists; choose a new directory")
    args.out.mkdir(parents=True)
    archive = args.out / "clinical-protocol-workbench-source.zip"
    subprocess.run(["git", "archive", "--format=zip", "--prefix=clinical-protocol-workbench/",
                    "--output=" + str(archive.resolve()), commit], cwd=root, check=True)
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=root, text=True).splitlines()
    checksums = []
    for path in paths:
        data = subprocess.check_output(["git", "show", commit + ":" + path], cwd=root)
        checksums.append(hashlib.sha256(data).hexdigest() + "  " + path)
    source = args.out / "SOURCE_SHA256SUMS.txt"
    source.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    (args.out / "COMMIT.txt").write_text(commit + "\n", encoding="utf-8")
    assets = [archive, source, args.out / "COMMIT.txt"]
    (args.out / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n" for path in assets), encoding="utf-8")
    print(f"Packaged {len(paths)} tracked files at {commit}")


if __name__ == "__main__":
    main()
