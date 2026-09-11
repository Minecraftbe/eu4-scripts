#!/usr/bin/env python3
"""
EU4 1444 明开局省份基础发展度提升脚本（支持只输出到 mod）

功能：
  - 扫描源目录 history/provinces
  - 只选 1444.11.11 时 owner = MNG 的省份
  - 只改顶层 base_tax / base_production / base_manpower
  - 输出模式：
      list    : 只列出候选省份，不写文件
      inplace : 直接改源目录里的省份文件
      mod     : 只把要改的省份文件复制/写入 mod 目录，其余省份不复制
  - 支持白名单/黑名单/文件清单
"""

import argparse
import re
import shutil
from pathlib import Path
from typing import NamedTuple, TypedDict

START_DATE: tuple[int, int, int] = (1444, 11, 11)

DATE_RE = re.compile(r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})\s*=\s*\{")
KV_INNER_RE = re.compile(r'([A-Za-z0-9_]+)\s*=\s*("[^"]*"|\S+)')

type DateKey = tuple[int, int, int]
type KVValue = str | list[str]
type KVMap = dict[str, KVValue]
type DatedBlocks = list[tuple[DateKey, KVMap]]


class ProvinceInfo(TypedDict):
    path: Path
    top: KVMap
    owner: str | None
    tax: int
    prod: int
    man: int
    is_city: str
    cot: int
    fort: str


class DevTriple(NamedTuple):
    tax: int
    prod: int
    man: int


def to_int(v: object, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except TypeError, ValueError:
        return default


def parse_kv_inner(text: str, target: KVMap) -> None:
    for m in KV_INNER_RE.finditer(text):
        key = m.group(1)
        val = m.group(2).strip().strip('"')
        if key in ("add_core", "remove_core"):
            existing = target.setdefault(key, [])
            if isinstance(existing, list):
                existing.append(val)
        else:
            target[key] = val


def parse_history_file(path: Path) -> tuple[KVMap, DatedBlocks]:
    """解析 EU4 省份历史文件，兼容单行/多行日期块。"""
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    top: KVMap = {}
    dated: DatedBlocks = []
    current_block: KVMap | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if current_block is None:
            m = DATE_RE.match(line.strip())
            if m:
                cur_date: DateKey = (
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                )
                current_block = {}
                dated.append((cur_date, current_block))
                rest = line[m.end() :].strip()
                if "}" in rest:
                    inner = rest.split("}", 1)[0]
                    parse_kv_inner(inner, current_block)
                    current_block = None
                else:
                    parse_kv_inner(rest, current_block)
                continue

            parse_kv_inner(line, top)

        else:
            if "}" in line:
                before = line.split("}", 1)[0]
                parse_kv_inner(before, current_block)
                current_block = None
            else:
                parse_kv_inner(line, current_block)

    return top, dated


def get_owner_at(top: KVMap, dated: DatedBlocks, start: DateKey) -> str | None:
    """返回开局日期时的 owner。"""
    owner_val = top.get("owner")
    owner: str | None = owner_val if isinstance(owner_val, str) else None

    for d, block in sorted(dated, key=lambda x: x[0]):
        if d <= start:
            ov = block.get("owner")
            if isinstance(ov, str):
                owner = ov
        else:
            break
    return owner


def distribute(new_total: int, tax: int, prod: int, man: int) -> DevTriple:
    """按原比例分配新的 tax/prod/man，每项最低为 1。"""
    orig = tax + prod + man
    if orig <= 0:
        t = max(1, new_total // 3)
        p = max(1, new_total // 3)
        m = max(1, new_total - t - p)
        return DevTriple(t, p, m)

    t = max(1, int(round(new_total * tax / orig)))
    p = max(1, int(round(new_total * prod / orig)))
    m = max(1, new_total - t - p)

    diff = new_total - (t + p + m)
    if diff:
        t += diff
        if t < 1:
            p += t - 1
            t = 1
        if p < 1:
            m += p - 1
            p = 1
        if m < 1:
            t += m - 1
            m = 1

    return DevTriple(t, p, m)


def rewrite_dev_text(text: str, tax: int, prod: int, man: int) -> str:
    """只改顶层 base_tax / base_production / base_manpower，返回新文本。"""
    lines = text.splitlines()
    out: list[str] = []
    depth = 0
    replaced: dict[str, bool] = {
        "base_tax": False,
        "base_production": False,
        "base_manpower": False,
    }

    for line in lines:
        if depth == 0:
            if re.match(r"^\s*base_tax\s*=", line):
                out.append(f"base_tax = {tax}")
                replaced["base_tax"] = True
                continue
            if re.match(r"^\s*base_production\s*=", line):
                out.append(f"base_production = {prod}")
                replaced["base_production"] = True
                continue
            if re.match(r"^\s*base_manpower\s*=", line):
                out.append(f"base_manpower = {man}")
                replaced["base_manpower"] = True
                continue

        out.append(line)
        depth += line.count("{") - line.count("}")

    if not all(replaced.values()):
        insert_at = len(out)
        for i, line in enumerate(out):
            if DATE_RE.match(line.strip()):
                insert_at = i
                break

        adds: list[str] = []
        if not replaced["base_tax"]:
            adds.append(f"base_tax = {tax}")
        if not replaced["base_production"]:
            adds.append(f"base_production = {prod}")
        if not replaced["base_manpower"]:
            adds.append(f"base_manpower = {man}")

        out[insert_at:insert_at] = adds

    return "\n".join(out) + "\n"


def load_id_list(text: str) -> set[int]:
    """从字符串里提取所有省份 ID，支持逗号、空格、换行分隔。"""
    ids: set[int] = set()
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        if tok.isdigit():
            ids.add(int(tok))
    return ids


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="提升 EU4 1444 明开局省份基础发展度")
    ap.add_argument(
        "--source-root",
        required=True,
        help="源目录（原版或 mod）根目录",
    )
    ap.add_argument(
        "--history",
        default="history/provinces",
        help="省份历史目录，相对 source-root",
    )

    ap.add_argument(
        "--output-mode",
        choices=["list", "inplace", "mod"],
        default="list",
        help="list=只列清单；inplace=直接改源目录；mod=只输出到 mod 目录",
    )

    ap.add_argument(
        "--mod-root",
        default=None,
        help="output-mode=mod 时的 mod 根目录",
    )
    ap.add_argument(
        "--overwrite-mod",
        action="store_true",
        help="output-mode=mod 时，如果 mod 里已有同名文件是否覆盖。默认不覆盖",
    )

    ap.add_argument("--owner", default="MNG", help="开局国家 tag，默认 MNG")

    ap.add_argument(
        "--provinces",
        default=None,
        help="只改这些省份 ID，逗号/空格分隔。留空=全部明省",
    )
    ap.add_argument(
        "--provinces-file",
        default=None,
        help="从文本文件读取省份 ID 白名单，每行可有多个 ID",
    )
    ap.add_argument(
        "--exclude",
        default=None,
        help="排除这些省份 ID，逗号/空格分隔",
    )

    ap.add_argument(
        "--target-dev",
        type=int,
        default=None,
        help="目标总发展度。不填则使用 factor 乘以原始总发展度",
    )
    ap.add_argument(
        "--factor",
        type=float,
        default=2.5,
        help="无 target-dev 时的总发展度乘数，默认 2.5",
    )

    ap.add_argument(
        "--backup",
        action="store_true",
        help="output-mode=inplace 时，写入前生成 .bak 备份",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="不写文件，只显示将要做什么",
    )

    return ap.parse_args()


def main() -> int:
    args = parse_args()

    source_root = Path(args.source_root)
    hist_dir = source_root / args.history

    if not hist_dir.exists():
        raise SystemExit(f"省份历史目录不存在: {hist_dir}")

    # ---------- 1. 读取全部省份 ----------
    all_provs: dict[int, ProvinceInfo] = {}
    for p in sorted(hist_dir.glob("*.txt")):
        m = re.match(r"(\d+)", p.name)
        if not m:
            continue
        pid = int(m.group(1))
        top, dated = parse_history_file(p)
        owner = get_owner_at(top, dated, START_DATE)
        all_provs[pid] = {
            "path": p,
            "top": top,
            "owner": owner,
            "tax": to_int(top.get("base_tax"), 0),
            "prod": to_int(top.get("base_production"), 0),
            "man": to_int(top.get("base_manpower"), 0),
            "is_city": str(top.get("is_city", "no")).lower(),
            "cot": to_int(top.get("center_of_trade"), 0),
            "fort": str(top.get("fort_15th", "no")).lower(),
        }

    # ---------- 2. 白名单/黑名单 ----------
    whitelist: set[int] | None = None
    if args.provinces:
        whitelist = load_id_list(args.provinces)
    if args.provinces_file:
        extra = load_id_list(Path(args.provinces_file).read_text(encoding="utf-8"))
        whitelist = extra if whitelist is None else (whitelist | extra)

    excluded: set[int] = load_id_list(args.exclude) if args.exclude else set()

    # ---------- 3. 筛选目标省份 ----------
    targets: dict[int, ProvinceInfo] = {}
    for pid, info in all_provs.items():
        if info["owner"] != args.owner:
            continue
        if whitelist is not None and pid not in whitelist:
            continue
        if pid in excluded:
            continue
        targets[pid] = info

    if not targets:
        raise SystemExit("没有匹配到任何省份。")

    # ---------- 4. 计算新发展度 ----------
    orig_total = sum(i["tax"] + i["prod"] + i["man"] for i in targets.values())
    final_total = (
        args.target_dev if args.target_dev else int(round(orig_total * args.factor))
    )

    weights: dict[int, float] = {}
    for pid, info in targets.items():
        orig = info["tax"] + info["prod"] + info["man"]
        bonus = 1.0
        if info["is_city"] == "yes":
            bonus *= 1.15
        if info["cot"] == 1:
            bonus *= 1.10
        elif info["cot"] == 2:
            bonus *= 1.20
        elif info["cot"] >= 3:
            bonus *= 1.35
        if info["fort"] == "yes":
            bonus *= 1.05
        weights[pid] = max(1.0, float(orig)) * bonus

    sum_w = sum(weights.values())

    plan: dict[int, DevTriple] = {}
    for pid in sorted(targets):
        info = targets[pid]
        new_total = max(3, int(round(final_total * weights[pid] / sum_w)))
        plan[pid] = distribute(new_total, info["tax"], info["prod"], info["man"])

    actual_total = sum(sum(v) for v in plan.values())

    # ---------- 5. 执行 ----------
    print(
        f"模式={args.output_mode}，匹配省份数={len(targets)}，"
        f"原始总dev={orig_total}，目标总dev={final_total}，实际总dev={actual_total}"
    )

    if args.output_mode == "list":
        for pid in sorted(targets):
            info = targets[pid]
            t, p, m = plan[pid]
            print(
                f"{pid:4d} {info['path'].name}: "
                f"{info['tax']}/{info['prod']}/{info['man']} -> {t}/{p}/{m}"
            )
        print("list 模式：未写入文件。")
        return 0

    mod_hist: Path | None = None
    if args.output_mode == "mod":
        if not args.mod_root:
            raise SystemExit("output-mode=mod 需要同时指定 --mod-root")
        mod_hist = Path(args.mod_root) / args.history
        if not args.dry_run:
            mod_hist.mkdir(parents=True, exist_ok=True)  # pyright: ignore[reportOptionalMemberAccess]

    for pid in sorted(targets):
        info = targets[pid]
        src = info["path"]
        t, p, m = plan[pid]

        new_text = rewrite_dev_text(
            src.read_text(encoding="utf-8-sig", errors="ignore"),
            t,
            p,
            m,
        )

        if args.output_mode == "inplace":
            dst = src
            if args.dry_run:
                print(f"[dry-run] 将写入 {dst}")
                continue

            if args.backup:
                bak = dst.with_suffix(dst.suffix + ".bak")
                if not bak.exists():
                    shutil.copy2(dst, bak)

            dst.write_text(new_text, encoding="utf-8")
            print(f"{pid:4d} {dst.name}: 已写入")

        elif args.output_mode == "mod":
            assert mod_hist is not None
            dst = mod_hist / src.name

            if dst.exists() and not args.overwrite_mod:
                print(
                    f"{pid:4d} {dst.name}: mod 中已存在，跳过"
                    f"（用 --overwrite-mod 覆盖）"
                )
                continue

            if args.dry_run:
                print(f"[dry-run] 将复制并写入 {dst}")
                continue

            dst.write_text(new_text, encoding="utf-8")
            print(f"{pid:4d} {dst.name}: 已写入 mod")

    if args.dry_run:
        print("dry-run：未真正写入文件。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
