#!/usr/bin/env python3
r"""把文本转成 EU4 能原样读取的“特殊字符转义”形式（C++ 版 EU4SpecialEscape 的 Python 复刻）。

用途：EU4 本地化文件（localisation/*.yml）里的中文、日文、希腊文等非 Latin 字符容易被
      解析器吃掉或显示成乱码；本脚本把它们转成 EU4 期望的转义序列（0x10~0x13 引导的
      3 字符组），可以只转一段文本，也可以批量转整个目录的本地化文件。转出来的字符内容与
      ParaTranz「EU4/CK2 编码转换」左侧框里的是同一串。

模式：--format 与 --encoding 描述的都是“转义的那一侧”；默认写出的 UTF-8+BOM 正是原工具
      xxx.utf8 的产物，重定向即可落进 mod 文件。控制字符在终端里不可见，所以默认模式屏幕上
      看会像少了东西（内容其实在），核对请用 --format display 或看重定向后的文件。

文件模式（--in-dir <目录>）：转义的是整份文件内容——不动 key:0 "..." 结构，CR/LF 原样保留，
      带 BOM 的按 UTF-8 读。

安全默认：文本模式不碰文件；文件模式默认只列清单，写入必须显式给 --out-dir 或 --in-place；
      原地改写默认先备份 .bak，内容没变化的文件不重写，重复运行结果一致。

已知边界：解码无法区分原文里本来就有的 0x10~0x13 控制字符，也无法区分 0xE101~0xE9FF 区间的
      原文与 0x101~0x9FF 平移后的码位。这两点是格式固有歧义，无法消除。

来源与许可：算法与常量表照搬 matanki-saito/EU4SpecialEscape 的 eu4specialescape.cpp
      （MIT，Copyright (c) 2018 matanki-saito），原始许可声明全文见本文件头部注释。
      https://github.com/matanki-saito/EU4SpecialEscape

示例：
  # 文本 -> 真实字节（UTF-8+BOM），可直接重定向进文件
  %(prog)s "原神" > escaped.txt
  # 纯 ASCII 写法，便于在终端看清内容
  %(prog)s "原神" --format display
  # 反向解码
  %(prog)s --decode --format display "\x10\u0178S\x10^y"
  # 文件模式：只列清单，不写任何文件
  %(prog)s --in-dir <本地化目录>
  # 文件模式：转义后写到独立目录
  %(prog)s --in-dir <本地化目录> --out-dir <输出目录>
  # 文件模式：原地改写（先备份 .bak）
  %(prog)s --in-dir <本地化目录> --in-place
  # 文件模式：还原成明文
  %(prog)s --in-dir <本地化目录> --decode --out-dir <输出目录>
"""

# =============================================================================
# 许可与出处（MIT）
#
# 本文件是 EU4SpecialEscape 的 Python 复刻，算法与常量表来自其 C++ 实现
# eu4specialescape.cpp；原项目以 MIT 协议开源：
#     https://github.com/matanki-saito/EU4SpecialEscape
#
# 按 MIT 协议要求，原始版权与许可声明全文保留如下：
#
# MIT License
#
# Copyright (c) 2018 matanki-saito
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# =============================================================================

import argparse
import io
import re
import shutil
import struct
import sys
from pathlib import Path

# ---------- 常量表（照搬原 C++，勿随意改动）----------

# CP1252 在 0x80~0x9F 区间的 27 个映射：CP1252 字节 -> Unicode 码位。
# 对应原 C++ 的 cp1252ToUCS2 / UCS2ToCP1252 两张 switch 表。
_CP1252_HIGH: dict[int, int] = {
    0x80: 0x20AC,
    0x82: 0x201A,
    0x83: 0x0192,
    0x84: 0x201E,
    0x85: 0x2026,
    0x86: 0x2020,
    0x87: 0x2021,
    0x88: 0x02C6,
    0x89: 0x2030,
    0x8A: 0x0160,
    0x8B: 0x2039,
    0x8C: 0x0152,
    0x8E: 0x017D,
    0x91: 0x2018,
    0x92: 0x2019,
    0x93: 0x201C,
    0x94: 0x201D,
    0x95: 0x2022,
    0x96: 0x2013,
    0x97: 0x2014,
    0x98: 0x02DC,
    0x99: 0x2122,
    0x9A: 0x0161,
    0x9B: 0x203A,
    0x9C: 0x0153,
    0x9E: 0x017E,
    0x9F: 0x0178,
}
_CP1252_LOW: dict[int, int] = {cp: byte for byte, cp in _CP1252_HIGH.items()}

# 触发转义的 25 个“冲突字节”：高字节或低字节命中就要调整引导符。
# 对应原 C++ convertWideTextToEscapedWideText 里两张 switch 的 case 列表。
_SPECIAL_BYTES: frozenset[int] = frozenset(
    {
        0x00,
        0x0A,
        0x0D,
        0x20,
        0x22,
        0x23,
        0x24,
        0x2F,
        0x3A,
        0x3B,
        0x3D,
        0x3F,
        0x40,
        0x5B,
        0x5C,
        0x5D,
        0x5F,
        0x7B,
        0x7D,
        0x7E,
        0x80,
        0xA3,
        0xA4,
        0xA7,
        0xBD,
    }
)

_ESCAPE_LEAD_BYTES: frozenset[int] = frozenset({0x10, 0x11, 0x12, 0x13})
_LOW_SHIFT = 14  # 低字节命中冲突字节时 +14
_HIGH_SHIFT = 9  # 高字节命中冲突字节时 -9
_PUA_OFFSET = 0xE000  # 0x100 < 码位 < 0xA00 时平移量
_PUA_LOW = 0x100
_PUA_HIGH = 0xA00
_UTF8_BOM = b"\xef\xbb\xbf"

_ENCODINGS: tuple[str, ...] = ("utf8", "utf8-no-bom", "cp1252")
# 转义一侧的两种表现形式：chars=真实字符/字节（默认）；display=纯 ASCII 写法（给人看）
_FORMATS: tuple[str, ...] = ("chars", "display")
_FORMAT_CHARS = "chars"
_FORMAT_DISPLAY = "display"
# BOM 属于整条输出流，只在开头写一次；只有 utf8 带 BOM
_BOM_PREFIX: dict[str, bytes] = {"utf8": _UTF8_BOM, "utf8-no-bom": b"", "cp1252": b""}

# 文件模式
_DEFAULT_GLOB = "*.yml"
_BACKUP_SUFFIX = ".bak"
# 工具集合标记：命中即拒绝写入，避免把 mod 数据写进工具仓库
_REPO_MARKER = ".dev-tools-repo"

# display 形式的唯一可逆写法：\\ 代表字面反斜杠，\xNN / \uNNNN 代表转义
_DISPLAY_RE = re.compile(r"\\\\|\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})")

# 过程说明默认安静，只有 --verbose 时才打印，且一律走 stderr。
VERBOSE = False


def log(*args: object) -> None:
    """过程说明：默认静默，--verbose 时打到 stderr。"""
    if VERBOSE:
        print(*args, file=sys.stderr)


def use_lf_newlines(stream: object) -> None:
    """让输出流用 LF 换行。

    本脚本的输出是要被重定向、被贴进文件里的产出，不跟随平台换行（对应原 C++
    用 fwrite 原样写出）；否则 Windows 上 display/decode 会变成 CRLF，
    而 --format chars 的字节流本来就是 LF，两边不一致。
    """
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(newline="\n")


# ---------- 编码表辅助 ----------


def _cp1252_to_ucs2(byte: int) -> int:
    """CP1252 字节 -> Unicode 码位（原 C++ cp1252ToUCS2，查不到就原样返回）。"""
    return _CP1252_HIGH.get(byte, byte)


def _ucs2_to_cp1252(cp: int) -> int:
    """Unicode 码位 -> CP1252 字节（原 C++ UCS2ToCP1252，查不到就原样返回）。"""
    return _CP1252_LOW.get(cp, cp)


def _ucs2_to_cp1252_byte(cp: int) -> int | None:
    """Unicode 码位 -> CP1252 字节，无法表示时返回 None。

    与 _ucs2_to_cp1252 的差别在失败语义：前者照抄原 C++ 的“原样返回”，
    无法区分“映射到同一个字节”和“没有对应字节”；识别转义序列时需要后者。
    0x00~0xFF 全部可按 Windows CP1252 还原成同名字节（C1 控制符也是）。
    """
    if cp <= 0xFF:
        return cp
    return _CP1252_LOW.get(cp)


def _utf16_units(text: str) -> list[int]:
    """把字符串拆成 UTF-16 码元，对齐 Windows 16 位 wchar_t 的处理单位。"""
    raw = text.encode("utf-16-le", "surrogatepass")
    return list(struct.unpack(f"<{len(raw) // 2}H", raw))


def _units_to_text(units: list[int]) -> str:
    """把 UTF-16 码元序列拼回字符串（_utf16_units 的逆操作，会合并代理对）。"""
    raw = struct.pack(f"<{len(units)}H", *units)
    return raw.decode("utf-16-le", "surrogatepass")


# ---------- 核心：转义与还原 ----------


def escape_text(text: str) -> str:
    """把文本转成 EU4 的特殊字符转义形式（原 C++ convertWideTextToEscapedWideText）。

    按 UTF-16 码元逐个处理（与 Windows 16 位 wchar_t 一致，含代理对）；命中 CP1252 的
    0x80~0x9F 特殊区、或高字节为 0 的码元原样保留；0x100 < 码位 < 0xA00 的码元先加
    0xE000 平移到私用区；其余按高/低字节是否命中 25 个冲突字节，产出 0x10~0x13 引导的
    3 字符组（依次为低字节、高字节，被补偿的那一侧已做 +14 / -9）。
    输入是原文，输出是转义后的字符序列；两端的字节编码由调用方决定。
    """
    out: list[str] = []

    for unit in _utf16_units(text):
        # CP1252 0x80~0x9F 特殊区里的字符原样保留
        if _ucs2_to_cp1252(unit) != unit:
            out.append(chr(unit))
            continue

        cp = unit

        # 0x100 < 码位 < 0xA00：先平移到私用区，避开会与转义序列混淆的区间
        if _PUA_LOW < cp < _PUA_HIGH:
            cp += _PUA_OFFSET

        high = (cp >> 8) & 0xFF
        low = cp & 0xFF

        # 单字节字符原样保留
        if high == 0:
            out.append(chr(cp))
            continue

        # 引导符本身编码了“哪一侧需要补偿”
        lead = 0x10
        if high in _SPECIAL_BYTES:
            lead += 2
        if low in _SPECIAL_BYTES:
            lead += 1

        match lead:
            case 0x11:
                low = (low + _LOW_SHIFT) & 0xFF
            case 0x12:
                high = (high - _HIGH_SHIFT) & 0xFF
            case 0x13:
                low = (low + _LOW_SHIFT) & 0xFF
                high = (high - _HIGH_SHIFT) & 0xFF
            case _:
                pass

        out.append(chr(lead))
        out.append(chr(_cp1252_to_ucs2(low)))
        out.append(chr(_cp1252_to_ucs2(high)))

    return "".join(out)


def unescape_text(text: str) -> str:
    """反向还原 escape_text 的结果（原 C++ 没有这个方向，是本脚本补的）。

    只在“引导符 0x10~0x13 + 后两个字符都能映射成 CP1252 字节”同时成立时才按转义解析，
    否则原样保留，尽量减少误判。两处歧义无法消除：原文里本来就有的 0x10~0x13 控制字符
    会被当成引导符；原文里 0xE101~0xE9FF 的码位会被当成平移后的结果。
    """
    out: list[int] = []
    units = _utf16_units(text)
    total = len(units)
    index = 0

    while index < total:
        lead = units[index]

        if lead in _ESCAPE_LEAD_BYTES and index + 2 < total:
            low_byte = _ucs2_to_cp1252_byte(units[index + 1])
            high_byte = _ucs2_to_cp1252_byte(units[index + 2])

            if low_byte is not None and high_byte is not None:
                if lead in (0x11, 0x13):
                    low_byte = (low_byte - _LOW_SHIFT) & 0xFF
                if lead in (0x12, 0x13):
                    high_byte = (high_byte + _HIGH_SHIFT) & 0xFF

                cp = (high_byte << 8) | low_byte
                if _PUA_OFFSET + _PUA_LOW < cp < _PUA_OFFSET + _PUA_HIGH:
                    cp -= _PUA_OFFSET

                out.append(cp)
                index += 3
                continue

        out.append(lead)
        index += 1

    return _units_to_text(out)


# ---------- 字节编码 ----------


def _cp1252_byte(ch: str) -> int:
    """单个字符 -> CP1252 字节，无法表示时报错。"""
    byte = _ucs2_to_cp1252_byte(ord(ch))
    if byte is None:
        raise ValueError(f"字符 U+{ord(ch):04X} 无法用 Windows-1252 表示")
    return byte


def encode_bytes(text: str, encoding: str) -> bytes:
    """把文本变成字节。

    不含 BOM：BOM 属于整条输出流，由写出方在开头加一次（多条输入共用一份）。
    """
    match encoding:
        case "utf8" | "utf8-no-bom":
            return text.encode("utf-8")
        case "cp1252":
            return bytes(_cp1252_byte(ch) for ch in text)
        case _:
            raise ValueError(f"未知编码: {encoding}")


def decode_bytes(raw: bytes, encoding: str) -> str:
    """按 --encoding 把字节变回文本（utf8 允许带 BOM）。"""
    match encoding:
        case "utf8" | "utf8-no-bom":
            return raw.removeprefix(_UTF8_BOM).decode("utf-8")
        case "cp1252":
            return "".join(chr(_cp1252_to_ucs2(byte)) for byte in raw)
        case _:
            raise ValueError(f"未知编码: {encoding}")


# ---------- 可见形式（display）----------


def to_display(text: str) -> str:
    r"""渲染成可见形式：可打印 ASCII 原样，反斜杠写作 \\，其余用 \xNN / \uNNNN。

    输出是纯 ASCII，且能由 from_display 逐字符还原。
    """
    out: list[str] = []

    for ch in text:
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif 0x20 <= cp <= 0x7E:
            out.append(ch)
        elif cp <= 0xFF:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(f"\\u{cp:04x}")

    return "".join(out)


def from_display(text: str) -> str:
    """to_display 的逆操作。"""

    def replace(match: re.Match[str]) -> str:
        hex_byte = match.group(1)
        hex_unit = match.group(2)
        if hex_byte is not None:
            return chr(int(hex_byte, 16))
        if hex_unit is not None:
            return chr(int(hex_unit, 16))
        return "\\"

    return _DISPLAY_RE.sub(replace, text)


# ---------- 命令行 ----------


def _help_texts() -> tuple[str, str]:
    """把模块 docstring 拆成 --help 的 description 与 epilog。

    docstring 保持完整用户文档：首段作 description（摘要），其余作 epilog（详细说明），
    因此 --help 先给摘要与选项，细节放在选项之后展开。
    """
    summary, _, detail = (__doc__ or "").strip().partition("\n\n")
    return summary.strip(), detail.strip()


def parse_args() -> argparse.Namespace:
    summary, detail = _help_texts()
    ap = argparse.ArgumentParser(
        description=summary,
        epilog=detail,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "text",
        nargs="*",
        help="一个或多个待转换文本；不给且没加 --stdin/--in-dir 时视为用法错误",
    )
    ap.add_argument(
        "--stdin",
        action="store_true",
        help=(
            "从标准输入读取：正向转义时按行读文本（UTF-8，容忍 BOM）；"
            "--decode --format chars 时读整段原始字节（默认：否）"
        ),
    )
    ap.add_argument(
        "--decode",
        action="store_true",
        help="反向：把转义文本/文件还原成原文（默认：否，即正向转义）",
    )
    ap.add_argument(
        "--format",
        choices=_FORMATS,
        default=_FORMAT_CHARS,
        help=(
            "转义一侧的表现形式："
            "chars=真实字符/字节，按 --encoding 落字节，可直接重定向进文件；"
            "display=纯 ASCII 写法 \\xNN / \\uNNNN，便于在终端查看与逐字对拍"
            f"（默认：{_FORMAT_CHARS}）"
        ),
    )
    ap.add_argument(
        "--encoding",
        choices=_ENCODINGS,
        default="utf8",
        help=(
            "转义一侧的字节编码："
            "utf8=UTF-8+BOM；"
            "utf8-no-bom=不带 BOM 的 UTF-8；"
            "cp1252=Windows-1252（默认：utf8）"
        ),
    )
    ap.add_argument(
        "--in-dir",
        default=None,
        help=(
            "文件模式：源目录，批量转码里面的本地化文件（默认递归、只看 *.yml）；"
            "给了它就不要再用文本参数（默认：无）"
        ),
    )
    target_group = ap.add_mutually_exclusive_group()
    target_group.add_argument(
        "--out-dir",
        default=None,
        help=(
            "文件模式：结果写到这个独立目录，保持相对路径；"
            "不得等于或位于 --in-dir 内，也不能落在工具集合内部（默认：无，即只列清单）"
        ),
    )
    target_group.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "文件模式：原地改写源文件，默认先备份 .bak；"
            "源目录落在工具集合内部时拒绝改写（默认：否）"
        ),
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="--in-place 时不生成 .bak 备份（默认：否，即默认备份）",
    )
    ap.add_argument(
        "--glob",
        default=_DEFAULT_GLOB,
        help=f"文件模式：匹配模式（默认：{_DEFAULT_GLOB}）",
    )
    ap.add_argument(
        "--no-recursive",
        action="store_true",
        help="文件模式：只看源目录顶层，不递归子目录（默认：否，即递归）",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="文件模式：对看起来已经转义过的文件也照做（默认：否，即跳过并告警）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="文件模式：只预演、不写任何文件（默认：否）",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="把参数回显与汇总打到 stderr（默认安静；--dry-run 隐含此项）",
    )

    return ap.parse_args()


def collect_texts(args: argparse.Namespace) -> list[str]:
    """正向转义的输入：位置参数在前，标准输入按行（UTF-8 文本）追加在后。"""
    texts = list(args.text)
    if args.stdin:
        texts.extend(sys.stdin.buffer.read().decode("utf-8-sig").splitlines())
    return texts


def collect_escaped(args: argparse.Namespace) -> list[str]:
    """反向解码的输入：取转义一侧，统一还原成“转义后的字符序列”。

    chars：标准输入按 --encoding 解成真实字符（位置参数本身就是真实字符，直接用）；
    display：位置参数与标准输入按 \\xNN / \\uNNNN 写法解析。
    """
    texts = list(args.text)
    if args.stdin:
        raw = sys.stdin.buffer.read()
        if args.format == _FORMAT_CHARS:
            texts.append(decode_bytes(raw, args.encoding))
        else:
            texts.extend(raw.decode("utf-8-sig").splitlines())

    if args.format == _FORMAT_DISPLAY:
        return [from_display(item) for item in texts]
    return texts


def write_lines(lines: list[str]) -> None:
    """逐条把原文写到 stdout。"""
    for line in lines:
        sys.stdout.write(line if line.endswith("\n") else line + "\n")


def write_escaped(escaped: list[str], fmt: str, encoding: str) -> None:
    """输出转义结果：chars 落真实字节（可直接重定向进文件），display 打印 ASCII 写法。

    chars 模式多条输入之间用单个换行分隔、末尾不加换行，与原工具写出的文件一致。
    """
    if fmt == _FORMAT_CHARS:
        body = b"\n".join(encode_bytes(item, encoding) for item in escaped)
        if sys.stdout.isatty():
            print(
                "[warn] chars 输出的是真实字节，0x10~0x13 引导符在终端里不可见，"
                "屏幕上看着会像少了东西；核对内容请加 --format display，"
                "或把输出重定向进文件后查看。",
                file=sys.stderr,
            )
        sys.stdout.buffer.write(_BOM_PREFIX[encoding] + body)
        return

    for item in escaped:
        print(to_display(item))


# ---------- 文件模式 ----------


def find_tool_collection(path: Path) -> Path | None:
    """从传入路径向上找工具集合标记；只依据调用方给的路径，不用脚本自身位置。"""
    for directory in (path, *path.parents):
        if (directory / _REPO_MARKER).is_file():
            return directory
    return None


def looks_escaped(text: str) -> bool:
    """粗判文本是否已经转义过：出现 0x10~0x13 引导符 + 两个可映射为 CP1252 字节的字符。"""
    units = _utf16_units(text)

    for index in range(len(units) - 2):
        if (
            units[index] in _ESCAPE_LEAD_BYTES
            and _ucs2_to_cp1252_byte(units[index + 1]) is not None
            and _ucs2_to_cp1252_byte(units[index + 2]) is not None
        ):
            return True

    return False


def collect_source_files(in_dir: Path, pattern: str, recursive: bool) -> list[Path]:
    """按通配模式收集文件（递归或只看顶层），排序保证结果可复现。"""
    matches = in_dir.rglob(pattern) if recursive else in_dir.glob(pattern)
    return sorted(path for path in matches if path.is_file())


def check_target(args: argparse.Namespace, in_dir: Path, out_dir: Path | None) -> int:
    """校验写入点：输出目录不得落在源目录内或工具集合内部。返回退出码（0=通过）。"""
    if out_dir is not None:
        if out_dir == in_dir or out_dir.is_relative_to(in_dir):
            print(
                f"[error] 输出目录等于或位于源目录内，拒绝写入: {out_dir}",
                file=sys.stderr,
            )
            return 2
        collection = find_tool_collection(out_dir)
        if collection is not None:
            print(
                f"[error] 输出目录位于工具集合内部（{collection}），拒绝写入: {out_dir}",
                file=sys.stderr,
            )
            return 2
        if out_dir.exists() and not out_dir.is_dir():
            print(f"[error] 输出路径已存在且不是目录: {out_dir}", file=sys.stderr)
            return 2

    if args.in_place:
        collection = find_tool_collection(in_dir)
        if collection is not None:
            print(
                f"[error] 源目录位于工具集合内部（{collection}），"
                f"原地改写会写进工具仓库，拒绝: {in_dir}",
                file=sys.stderr,
            )
            return 2

    return 0


def convert_one_file(args: argparse.Namespace, raw: bytes) -> tuple[str, bytes]:
    """单个文件的转码：返回 (是否转义过的源文件, 输出字节)。"""
    text = raw.removeprefix(_UTF8_BOM).decode("utf-8")
    converted = unescape_text(text) if args.decode else escape_text(text)
    return text, _BOM_PREFIX[args.encoding] + encode_bytes(converted, args.encoding)


def run_file_mode(args: argparse.Namespace) -> int:
    """批量转码目录里的本地化文件：默认只列清单，写入必须显式指定目标。"""
    if args.text or args.stdin:
        print(
            "[error] --in-dir 是文件模式：不要再给文本参数或 --stdin", file=sys.stderr
        )
        return 2

    in_dir = Path(args.in_dir).expanduser().resolve()
    if not in_dir.is_dir():
        print(f"[error] 源目录不存在或不是目录: {in_dir}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    code = check_target(args, in_dir, out_dir)
    if code:
        return code

    files = collect_source_files(in_dir, args.glob, not args.no_recursive)
    if not files:
        print(f"[error] {in_dir} 下没有匹配 {args.glob} 的文件", file=sys.stderr)
        return 1

    if args.in_place:
        target = "原地改写"
    elif out_dir is not None:
        target = str(out_dir)
    else:
        target = "只列清单"

    log(
        f"[ok] 文件模式 方向={'解码' if args.decode else '转义'}  encoding={args.encoding}  "
        f"glob={args.glob}  文件={len(files)}  目标={target}  dry-run={args.dry_run}"
    )

    converted = skipped = unchanged = errors = 0

    for src in files:
        rel = src.relative_to(in_dir)

        try:
            raw = src.read_bytes()
        except OSError as exc:
            print(f"[error] {rel}: 读取失败: {exc}", file=sys.stderr)
            errors += 1
            continue

        try:
            text, out_bytes = convert_one_file(args, raw)
        except UnicodeDecodeError:
            print(f"[warn] {rel}: 不是 UTF-8，跳过", file=sys.stderr)
            skipped += 1
            continue
        except ValueError as exc:
            print(f"[error] {rel}: {exc}", file=sys.stderr)
            errors += 1
            continue

        escaped_source = looks_escaped(text)

        if not args.decode:
            if escaped_source and not args.force:
                print(
                    f"[warn] {rel}: 看起来已经转义过，跳过（确认要重复处理再加 --force）",
                    file=sys.stderr,
                )
                skipped += 1
                continue
            if escaped_source:
                print(
                    f"[warn] {rel}: --force 已跳过“看起来已转义”的保护，可能造成二次转义",
                    file=sys.stderr,
                )
        elif not escaped_source:
            print(f"[warn] {rel}: 没发现转义序列，解码后内容不变", file=sys.stderr)

        if args.in_place:
            dst = src
        elif out_dir is not None:
            dst = out_dir / rel
        else:
            dst = None

        if dst is None or args.dry_run:
            prefix = "dry-run" if args.dry_run else "ok"
            shown = f"将写入 {dst}" if dst is not None else "未写入"
            print(f"[{prefix}] {rel}  {len(raw)} -> {len(out_bytes)} 字节（{shown}）")
            converted += 1
            continue

        if args.in_place and out_bytes == raw:
            log(f"[skip] {rel}: 内容不变")
            unchanged += 1
            continue

        if args.in_place and not args.no_backup:
            backup = dst.with_name(dst.name + _BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(dst, backup)

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(out_bytes)
        print(f"[ok] {rel}  {len(raw)} -> {len(out_bytes)} 字节 -> {dst}")
        converted += 1

    log(f"[ok] 完成：转换 {converted}，跳过 {skipped}，未变 {unchanged}，错误 {errors}")
    return 1 if errors else 0


def main() -> int:
    args = parse_args()

    global VERBOSE
    VERBOSE = args.verbose or args.dry_run  # pyright: ignore[reportConstantRedefinition]

    use_lf_newlines(sys.stdout)
    use_lf_newlines(sys.stderr)

    if (args.out_dir or args.in_place or args.no_backup) and not args.in_dir:
        print(
            "[error] --out-dir / --in-place / --no-backup 只用于文件模式，需要同时给 --in-dir",
            file=sys.stderr,
        )
        return 2

    if args.in_dir:
        return run_file_mode(args)

    if not args.text and not args.stdin:
        print(
            "[error] 没有输入：请给出文本参数，或加 --stdin 从标准输入读取",
            file=sys.stderr,
        )
        return 2

    log(
        f"[ok] 方向={'解码' if args.decode else '转义'}  "
        f"format={args.format}  encoding={args.encoding}"
    )

    count = 0

    try:
        if args.decode:
            plain = [unescape_text(item) for item in collect_escaped(args)]
            count = len(plain)
            write_lines(plain)
        else:
            escaped = [escape_text(item) for item in collect_texts(args)]
            count = len(escaped)
            write_escaped(escaped, args.format, args.encoding)
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    log(f"[ok] 完成：处理 {count} 条输入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
