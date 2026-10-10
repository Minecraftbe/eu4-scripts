#!/usr/bin/env python3
"""初始化项目目录。

功能：
  1. 在目标目录创建软链接 dev-tools -> 指定的工具目录
  2. 将 dev-tools 写入目标目录的 .gitignore
  3. 在目标目录执行 git init

用法：
  python init_mod_project.py --tools-dir /path/to/tools [目标目录] [--dry-run]

安全默认：
  默认不覆盖任何已有内容：软链接已存在且正确、.gitignore 已有条目、已是 git 仓库时
  只打印 [skip]；目标已存在且状态冲突则报错退出。--dry-run 下不产生任何副作用。
  目标目录位于工具集合内部（带 .dev-tools-repo 标记）时拒绝执行。

输出：
  默认安静：每一步的结果与汇总只在 --verbose 时打印（--dry-run 隐含 --verbose）；
  过程说明与警告走 stderr，stdout 不产生任何输出。

注意：
  工具目录必须通过 --tools-dir 显式指定，
  不会自动使用脚本自身所在位置，避免误操作。
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

LINK_NAME = "dev-tools"

# 工具集合标记文件（工具仓库根目录）。拒绝把链接建在工具集合内部。
TOOL_MARKER = ".dev-tools-repo"

# 过程说明默认安静，只有 --verbose（或 --dry-run）时才打印，且一律走 stderr。
VERBOSE = False


def log(*args: object) -> None:
    """过程说明：默认静默，--verbose 时打到 stderr。"""
    if VERBOSE:
        print(*args, file=sys.stderr)


def warn(*args: object) -> None:
    """警告：始终打到 stderr，不受 --verbose 影响。"""
    print(*args, file=sys.stderr)


def find_tool_collection(path: Path) -> Path | None:
    """向上查找工具集合标记，命中则说明 path 在工具集合内部。

    只看传入的路径，不看脚本自身位置，因此脚本脱离仓库后本检查自然失效。
    """
    for d in (path, *path.parents):
        if (d / TOOL_MARKER).is_file():
            return d
    return None


def is_link(path: Path) -> bool:
    """path 是否为符号链接或 junction。

    Windows 上 junction 不算符号链接（Path.is_symlink() 返回 False），
    而本脚本在没有 symlink 权限时正是回退到 junction，因此必须一起判断。
    """
    return path.is_symlink() or path.is_junction()


def create_symlink(link_target: Path, link_path: Path, dry_run: bool) -> bool:
    """在 link_path 创建指向 link_target 的链接（优先 symlink，Windows 回退 junction）。

    返回本步是否有改动要做（dry-run 下表示将会改动）。
    """
    if is_link(link_path):
        if link_path.resolve() == link_target.resolve():
            log(f"[skip] 链接已存在且正确：{link_path} -> {link_target}")
            return False
        raise SystemExit(
            f"[error] 已存在指向其他位置的链接：{link_path} -> {link_path.resolve()}"
        )

    if link_path.exists():
        raise SystemExit(f"[error] 路径已存在：{link_path}")

    if dry_run:
        log(f"[dry-run] 将创建软链接：{link_path} -> {link_target}")
        return True

    try:
        os.symlink(link_target, link_path, target_is_directory=True)
        log(f"[ok] 已创建软链接：{link_path} -> {link_target}")
        return True
    except OSError as exc:
        if platform.system() != "Windows":
            raise SystemExit(f"[error] 创建软链接失败：{exc}") from exc
        warn(f"[warn] symlink 失败（{exc}），尝试使用 junction")

    # Windows 回退：使用 junction（不需要管理员权限）
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(link_target.resolve())],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[error] 创建 junction 失败：{result.stderr.strip()}")
    log(f"[ok] 已创建 junction：{link_path} -> {link_target}")
    return True


def ensure_gitignore(gitignore: Path, entry: str, dry_run: bool) -> bool:
    """确保 .gitignore 中包含 entry；返回本步是否有改动要做。"""
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8-sig")
        if any(line.strip() == entry for line in content.splitlines()):
            log(f"[skip] .gitignore 已包含：{entry}")
            return False
        if dry_run:
            log(f"[dry-run] 将追加到 .gitignore：{entry}")
            return True
        suffix = "" if content.endswith("\n") or content == "" else "\n"
        gitignore.write_text(content + suffix + entry + "\n", encoding="utf-8")
        log(f"[ok] 已追加到 .gitignore：{entry}")
    else:
        if dry_run:
            log(f"[dry-run] 将创建 .gitignore 并写入：{entry}")
            return True
        gitignore.write_text(entry + "\n", encoding="utf-8")
        log(f"[ok] 已创建 .gitignore 并写入：{entry}")
    return True


def run_git_init(target_dir: Path, dry_run: bool) -> bool:
    """在目标目录执行 git init；返回本步是否有改动要做。"""
    if (target_dir / ".git").exists():
        log(f"[skip] 已是 git 仓库：{target_dir}")
        return False
    if dry_run:
        log(f"[dry-run] 将执行 git init：{target_dir}")
        return True
    result = subprocess.run(
        ["git", "init"],
        cwd=target_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[error] git init 失败：{result.stderr.strip()}")
    log(f"[ok] 已执行 git init：{target_dir}")
    return True


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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印每一步的结果与汇总（默认安静；--dry-run 隐含此项）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    global VERBOSE
    VERBOSE = args.verbose or args.dry_run

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

    collection = find_tool_collection(target_dir)
    if collection is not None:
        raise SystemExit(
            f"[error] 目标目录位于工具集合内部（{collection}），拒绝写入：{target_dir}"
        )

    link_path = target_dir / LINK_NAME
    gitignore = target_dir / ".gitignore"

    log(f"工具目录（软链接目标）：{tools_dir}")
    log(f"目标目录：{target_dir}")
    log()

    steps = [
        create_symlink(tools_dir, link_path, args.dry_run),
        ensure_gitignore(gitignore, LINK_NAME, args.dry_run),
        run_git_init(target_dir, args.dry_run),
    ]

    done = sum(1 for step in steps if step)
    skipped_steps = len(steps) - done

    log()
    if args.dry_run:
        log(f"[dry-run] 未做任何改动（将执行 {done} 步，跳过 {skipped_steps} 步）。")
    else:
        log(f"完成：{len(steps)} 步（执行 {done}，跳过 {skipped_steps}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
