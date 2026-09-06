#!/usr/bin/env python3
"""Install a pinned Codex skill bundle; never overwrite existing skill trees."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

CORE = "clinical-protocol-workbench"
LOCK = "assets/bundle.lock.json"
IGNORED_DIRS = {".git", "__pycache__"}


class BundleError(Exception):
    pass


def ignored(name: str) -> bool:
    return name in IGNORED_DIRS or name == ".DS_Store" or name.endswith((".pyc", ".pyo"))


def fingerprints(root: Path, exclude: set[str] | None = None) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise BundleError("Skill 路径不是普通目录")
    result = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not ignored(d))
        for name in dirs + files:
            if not ignored(name) and (Path(current) / name).is_symlink():
                raise BundleError("Skill 中存在符号链接")
        for name in sorted(files):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if ignored(name) or relative in (exclude or set()):
                continue
            if not path.is_file():
                raise BundleError("Skill 中存在非普通文件")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def load_lock(source: Path) -> dict:
    lock = json.loads((source / LOCK).read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise BundleError("不支持的安装清单版本")
    names = set()
    for item in lock["skills"]:
        name = item["name"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name in names:
            raise BundleError("安装清单中的 Skill 名称非法或重复")
        names.add(name)
        if name != CORE:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", item["repo"]):
                raise BundleError("依赖仓库格式错误")
            if not re.fullmatch(r"[0-9a-f]{40}", item["commit"]):
                raise BundleError("依赖必须固定到完整提交")
            sub = Path(item["path"])
            if sub.is_absolute() or ".." in sub.parts or "\\" in item["path"]:
                raise BundleError("依赖子目录越界")
        if "SKILL.md" not in item["files"]:
            raise BundleError("依赖缺少 SKILL.md 指纹")
        for filename, digest in item["files"].items():
            if (Path(filename).is_absolute() or ".." in Path(filename).parts
                    or "\\" in filename or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise BundleError("文件指纹格式错误")
    if CORE not in names:
        raise BundleError("安装清单缺少核心 Skill")
    core = next(item for item in lock["skills"] if item["name"] == CORE)
    if fingerprints(source, {LOCK}) != core["files"]:
        raise BundleError("安装源核心文件校验失败，请重新取得固定发行版")
    # The lock cannot hash itself. The release checksum authenticates it; once
    # loaded, also compare its bytes when checking another installed core tree.
    core["files"] = {**core["files"], LOCK: hashlib.sha256((source / LOCK).read_bytes()).hexdigest()}
    return lock


def inventory(items: list[dict], dest: Path) -> list[dict]:
    rows = []
    for item in items:
        path = dest / item["name"]
        status = "missing"
        if path.exists() or path.is_symlink():
            try:
                status = "matched" if fingerprints(path) == item["files"] else "conflict"
            except BundleError:
                status = "conflict"
        rows.append({"name": item["name"], "status": status})
    return rows


def find_installer(explicit: Path | None) -> Path:
    candidates = [explicit] if explicit else [
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        / "skills/.system/skill-installer/scripts/install-skill-from-github.py",
        Path.home() / ".codex/skills/.system/skill-installer/scripts/install-skill-from-github.py",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise BundleError("找不到 Codex skill-installer；请用 --installer 指定其现有脚本路径")


def fetch(item: dict, stage: Path, installer: Path) -> None:
    # Git sparse checkout of '.' omits child directories in the bundled
    # installer. Root skills therefore require its complete archive route.
    method = "download" if item["path"] == "." else "git"
    command = [sys.executable, str(installer), "--repo", item["repo"],
               "--ref", item["commit"], "--path", item["path"],
               "--name", item["name"], "--dest", str(stage), "--method", method]
    print(f"正在取得固定版本：{item['name']}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(command, capture_output=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"{item['name']} 下载无法完成；可在恢复网络/安装器后重试") from exc
    if result.returncode:
        if method == "download" and not (stage / item["name"]).exists():
            print(f"完整下载未完成，改用固定提交的完整 Git 检出：{item['name']}", file=sys.stderr, flush=True)
            full_git_root(item, stage)
            return
        # Do not forward subprocess output: configured Git helpers can include
        # credentials in diagnostics. Use the existing secure login to recover.
        raise BundleError(f"{item['name']} 下载失败；请检查网络、git 与现有 GitHub 授权")


def full_git_root(item: dict, stage: Path) -> None:
    """Root-skill fallback avoids the installer's sparse '.' limitation."""
    target = stage / item["name"]
    commands = [
        ["git", "init", str(target)],
        ["git", "-C", str(target), "remote", "add", "origin", f"https://github.com/{item['repo']}.git"],
        ["git", "-C", str(target), "fetch", "--depth=1", "origin", item["commit"]],
        ["git", "-C", str(target), "checkout", "--detach", item["commit"]],
    ]
    try:
        for command in commands:
            result = subprocess.run(command, capture_output=True, timeout=120)
            if result.returncode:
                raise BundleError(f"{item['name']} 完整 Git 检出失败，请检查网络及现有授权")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"{item['name']} 完整 Git 检出未完成") from exc


def install(items: list[dict], source: Path, dest: Path, installer: Path | None) -> list[dict]:
    if dest.is_symlink():
        raise BundleError("安装根目录不能是符号链接")
    dest.mkdir(parents=True, exist_ok=True)
    guard = dest / ".clinical-protocol-bundle-install.lock"
    try:
        guard.mkdir()
    except FileExistsError as exc:
        raise BundleError("安装锁已存在；先确认没有其他安装进程，勿并发安装") from exc
    created = []
    try:
        rows = inventory(items, dest)
        if any(row["status"] == "conflict" for row in rows):
            raise BundleError("存在版本冲突，所有现有目录均保留；先核对或备份冲突目录")
        missing = {row["name"] for row in rows if row["status"] == "missing"}
        with tempfile.TemporaryDirectory(prefix=".workbench-bundle-", dir=dest.parent) as temp:
            stage = Path(temp)
            for item in items:
                name = item["name"]
                if name not in missing:
                    continue
                if name == CORE:
                    shutil.copytree(source, stage / name, ignore=lambda _, names: [n for n in names if ignored(n)])
                else:
                    fetch(item, stage, find_installer(installer))
                if fingerprints(stage / name) != item["files"]:
                    raise BundleError(f"{name} 下载文件指纹不符，未安装任何组件")
            # Every missing component is verified before any final tree is created.
            for item in items:
                name = item["name"]
                if name not in missing:
                    continue
                target = dest / name
                target.mkdir()  # Exclusive creation; never replace another tree.
                created.append(target)
                shutil.copytree(stage / name, target, dirs_exist_ok=True,
                                ignore=lambda _, names: [n for n in names if ignored(n)])
            result = inventory(items, dest)
            if any(row["status"] != "matched" for row in result):
                raise BundleError("安装后回读失败，本次新增目录将回滚")
            return [{**row, "action": "installed" if row["name"] in missing else "skipped"}
                    for row in result]
    except Exception:
        for path in reversed(created):
            shutil.rmtree(path)
        raise
    finally:
        guard.rmdir()


def environment_status() -> dict:
    return {
        "python": sys.version.split()[0],
        "git": bool(shutil.which("git")),
        "autocorrect": bool(shutil.which("autocorrect")),
        "python_libraries": {name: importlib.util.find_spec(name) is not None
                             for name in ("docx", "pypdf", "pymupdf", "lxml", "requests", "bibtexparser", "paperqa")},
        "word": "detected_not_validated" if sys.platform == "darwin" and
        (Path("/Applications/Microsoft Word.app").exists() or
         (Path.home() / "Applications/Microsoft Word.app").exists()) else "not_checked",
        "note": "仅检查当前 Python 与本机可见程序；未安装运行库、申请授权或测试外部数据库、Word 渲染。",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 clinical-protocol-workbench 固定版本组合（默认 standard）")
    parser.add_argument("--profile", choices=("core", "standard"), default="standard")
    parser.add_argument("--dest", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills")
    parser.add_argument("--installer", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="离线预览，不安装")
    mode.add_argument("--check", action="store_true", help="离线检查，缺失/冲突时非零退出")
    parser.add_argument("--report", type=Path, help="另存新 JSON 报告；不覆盖已有文件")
    args = parser.parse_args(argv)
    report = {"profile": args.profile, "destination": str(args.dest.absolute()), "skills": []}
    code = 1
    try:
        if args.report and (args.report.exists() or args.report.is_symlink()):
            raise BundleError("报告已存在，请选择新文件名")
        source = Path(__file__).resolve().parents[1]
        lock = load_lock(source)
        items = [item for item in lock["skills"] if args.profile == "standard" or item["name"] == CORE]
        report.update(version=lock["version"], skills=inventory(items, args.dest), environment=environment_status())
        if args.check or args.dry_run:
            bad = any(row["status"] == "conflict" or (args.check and row["status"] == "missing") for row in report["skills"])
            report["status"] = "attention_required" if bad else ("verified" if args.check else "preview")
            code = 1 if bad else 0
        else:
            report["skills"] = install(items, source, args.dest, args.installer)
            report["status"] = "installed_and_verified"
            code = 0
    except (BundleError, OSError, ValueError, KeyError) as exc:
        report.update(status="failed", error=str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        try:
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except OSError:
            print("报告未写入，请使用标准输出；安装状态以上方结果为准。", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
