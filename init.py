#!/usr/bin/env python3
"""初始化项目目录。

功能：
  1. 在目标目录创建软链接 dev-tools -> 指定的工具目录
  2. 将 dev-tools 写入目标目录的 .gitignore
  3. 在目标目录执行 git init

用法：
  python init.py --tools-dir /path/to/tools [目标目录] [--dry-run]

注意：
  工具目录必须通过 --tools-dir 显式指定，
  不会自动使用脚本自身所在位置，避免误操作。
"""

import argparse
import os
import platform
import subprocess
from pathlib import Path

LINK_NAME = "dev-tools"


def create_symlink(target_dir: Path, link_path: Path, dry_run: bool) -> None:
    """在 link_path 创建指向 target_dir 的软链接。"""
    if link_path.is_symlink():
        if link_path.resolve() == target_dir.resolve():
            print(f"[skip] 软链接已存在且正确：{link_path} -> {target_dir}")
            return
        raise SystemExit(
            f"[error] 已存在指向其他位置的软链接：{link_path} -> {link_path.resolve()}"
        )

    if link_path.exists():
        raise SystemExit(f"[error] 路径已存在：{link_path}")

    if dry_run:
        print(f"[dry-run] 将创建软链接：{link_path} -> {target_dir}")
        return

    try:
        os.symlink(target_dir, link_path, target_is_directory=True)
        print(f"[ok] 已创建软链接：{link_path} -> {target_dir}")
        return
    except OSError as exc:
        if platform.system() != "Windows":
            raise SystemExit(f"[error] 创建软链接失败：{exc}") from exc
        print(f"[warn] symlink 失败（{exc}），尝试使用 junction")

    # Windows 回退：使用 junction（不需要管理员权限）
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_dir.resolve())],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[error] 创建 junction 失败：{result.stderr.strip()}")
    print(f"[ok] 已创建 junction：{link_path} -> {target_dir}")


def ensure_gitignore(gitignore: Path, entry: str, dry_run: bool) -> None:
    """确保 .gitignore 中包含 entry。"""
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if any(line.strip() == entry for line in content.splitlines()):
            print(f"[skip] .gitignore 已包含：{entry}")
            return
        if dry_run:
            print(f"[dry-run] 将追加到 .gitignore：{entry}")
            return
        suffix = "" if content.endswith("\n") or content == "" else "\n"
        gitignore.write_text(content + suffix + entry + "\n", encoding="utf-8")
        print(f"[ok] 已追加到 .gitignore：{entry}")
    else:
        if dry_run:
            print(f"[dry-run] 将创建 .gitignore 并写入：{entry}")
            return
        gitignore.write_text(entry + "\n", encoding="utf-8")
        print(f"[ok] 已创建 .gitignore 并写入：{entry}")


def run_git_init(target_dir: Path, dry_run: bool) -> None:
    """在目标目录执行 git init。"""
    if (target_dir / ".git").exists():
        print(f"[skip] 已是 git 仓库：{target_dir}")
        return
    if dry_run:
        print(f"[dry-run] 将执行 git init：{target_dir}")
        return
    result = subprocess.run(
        ["git", "init"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[error] git init 失败：{result.stderr.strip()}")
    print(f"[ok] 已执行 git init：{target_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tools-dir",
        required=True,
        help="工具目录（软链接指向的目标），必须显式指定",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="目标项目目录（默认当前目录）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示计划，不执行",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    tools_dir = Path(args.tools_dir).expanduser().resolve()
    target_dir = Path(args.target).expanduser().resolve()

    if not tools_dir.exists():
        raise SystemExit(f"[error] 工具目录不存在：{tools_dir}")
    if not tools_dir.is_dir():
        raise SystemExit(f"[error] 工具路径不是目录：{tools_dir}")

    if not target_dir.exists():
        raise SystemExit(f"[error] 目标目录不存在：{target_dir}")
    if not target_dir.is_dir():
        raise SystemExit(f"[error] 目标不是目录：{target_dir}")

    if target_dir == tools_dir:
        raise SystemExit(f"[error] 目标目录与工具目录相同：{target_dir}")

    link_path = target_dir / LINK_NAME
    gitignore = target_dir / ".gitignore"

    print(f"工具目录（软链接目标）：{tools_dir}")
    print(f"目标目录：{target_dir}")
    print()

    create_symlink(tools_dir, link_path, args.dry_run)
    ensure_gitignore(gitignore, LINK_NAME, args.dry_run)
    run_git_init(target_dir, args.dry_run)

    print()
    print("初始化完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
