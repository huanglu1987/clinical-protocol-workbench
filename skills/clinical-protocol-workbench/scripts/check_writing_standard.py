#!/usr/bin/env python3
"""Verify the required local writing/review standard, optionally show a range."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
LOCK = SKILL / "assets/writing-standard.lock.json"


def standard_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "clinical-protocol-workbench/standards"


def verify(path: Path, lock: dict) -> tuple[bytes, list[str]]:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != lock["sha256"]:
        raise ValueError("规范文件版本/内容不符；请提供锁定版本，不要重算清单绕过检查")
    lines = data.decode("utf-8").splitlines()
    if len(lines) != lock["line_count"]:
        raise ValueError("规范行数不符")
    return data, lines


def locate(explicit: Path | None, lock: dict) -> Path:
    if explicit is not None:
        return explicit
    candidates = [standard_home() / lock["filename"], Path.cwd() / lock["filename"],
                  Path.home() / "Desktop" / lock["filename"]]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("缺少必读规范；请使用有权使用的本地原文件并以 --path 指定，或 --install-source 登记本机副本")


def install_source(source: Path, lock: dict) -> Path:
    data, _ = verify(source, lock)
    target = standard_home() / lock["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        verify(target, lock)
        return target
    # A local support file, deliberately outside the public/discoverable skill tree.
    with target.open("xb") as handle:
        handle.write(data)
    verify(target, lock)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    paths = parser.add_mutually_exclusive_group()
    paths.add_argument("--path", type=Path, help="Use this exact local file for this check")
    paths.add_argument("--install-source", type=Path, help="Verify and copy an authorized original to local support storage")
    parser.add_argument("--show", action="store_true", help="Output text with line numbers; must actually be read by the agent")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    args = parser.parse_args(argv)
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        path = install_source(args.install_source, lock) if args.install_source else locate(args.path, lock)
        _, lines = verify(path, lock)
        end = args.end if args.end is not None else len(lines)
        if args.start < 1 or end < args.start or end > len(lines):
            raise ValueError("读取范围越界")
        report = {"status": "matched", "path": str(path.resolve()), "version": lock["content_version"],
                  "sha256": lock["sha256"], "line_count": len(lines),
                  "agent_read_complete": "not_attested",
                  "note": "本脚本仅校验文件及提供文本，不证明智能体已读完；每轮须实际完整读取并在项目记录中留痕。"}
        if args.show:
            report["emitted_range"] = [args.start, end]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.show:
            for number in range(args.start, end + 1):
                print(f"{number}: {lines[number - 1]}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "agent_read_complete": "not_attested"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
