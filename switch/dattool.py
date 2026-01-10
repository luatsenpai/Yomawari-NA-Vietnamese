#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PS_FS_V1? (.dat) pack/unpack tool (raw only)

Menu:
1) Unpack file dat -> xuất ra thư mục cùng tên (bỏ .dat)
2) Pack thư mục -> xuất file dat cùng tên thêm _new

Format:
- Header: 0x10 bytes, starts with b"PS_FS_V1?"
- Table: entries 0x40 bytes:
    name[0x30] (null-terminated UTF-8)
    size  u64 LE
    offset u64 LE
- Terminator entry: all zeros
"""
from __future__ import annotations

import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, List, Tuple

MAGIC_PREFIX = b"PS_FS_V1?"
HEADER_SIZE = 0x10
ENTRY_SIZE  = 0x40
NAME_LEN    = 0x30
ALIGN       = 0x10  # alignment khi pack (an toàn)


@dataclass
class Entry:
    index: int
    name: str
    size: int
    offset: int


def u64le(x: int) -> bytes:
    return struct.pack("<Q", x)


def read_u64_le(b: bytes) -> int:
    return struct.unpack("<Q", b)[0]


def parse_archive(fin: BinaryIO) -> Tuple[bytes, List[Entry]]:
    header = fin.read(HEADER_SIZE)
    if len(header) != HEADER_SIZE:
        raise ValueError("File quá nhỏ.")
    if not header.startswith(MAGIC_PREFIX):
        raise ValueError(f"Không đúng PS_FS_V1? .dat. Header={header!r}")

    entries: List[Entry] = []
    idx = 0
    while True:
        rec = fin.read(ENTRY_SIZE)
        if len(rec) < ENTRY_SIZE:
            break

        name_raw = rec[:NAME_LEN].split(b"\x00", 1)[0]
        size = read_u64_le(rec[NAME_LEN:NAME_LEN + 8])
        offset = read_u64_le(rec[NAME_LEN + 8:NAME_LEN + 16])

        if len(name_raw) == 0 and size == 0 and offset == 0:
            break

        name = name_raw.decode("utf-8", "replace")
        entries.append(Entry(idx, name, size, offset))
        idx += 1

    return header, entries


def split_rel_path(name: str) -> List[str]:
    parts = re.split(r"[\\/]+", name.strip())
    return [p for p in parts if p]


def sanitize_part(part: str) -> str:
    part = part.strip()
    if not part:
        return "_"
    part = re.sub(r'[<>:"/\\\\|?*]', "_", part)
    part = part.rstrip(" .")
    return part or "_"


def safe_output_path(base_dir: Path, name: str) -> Path:
    parts = [sanitize_part(p) for p in split_rel_path(name)]
    if not parts:
        parts = ["_"]
    return base_dir.joinpath(*parts)


def ensure_unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem = p.stem
    suffix = p.suffix
    parent = p.parent
    for i in range(1, 10_000):
        cand = parent / f"{stem}__dup{i}{suffix}"
        if not cand.exists():
            return cand
    raise RuntimeError(f"Quá nhiều trùng tên: {p}")


def copy_range(fin: BinaryIO, offset: int, size: int, fout: BinaryIO, chunk: int = 1024 * 1024) -> None:
    fin.seek(offset)
    remaining = size
    while remaining > 0:
        n = min(chunk, remaining)
        buf = fin.read(n)
        if not buf:
            raise IOError("EOF bất ngờ khi trích.")
        fout.write(buf)
        remaining -= len(buf)


def unpack_dat(dat_path: Path) -> Path:
    out_dir = dat_path.with_suffix("")  # thư mục cùng tên (bỏ .dat)
    out_dir.mkdir(parents=True, exist_ok=True)

    with dat_path.open("rb") as fin:
        _, entries = parse_archive(fin)
        fin.seek(0, os.SEEK_END)
        file_size = fin.tell()

        for e in entries:
            if e.offset + e.size > file_size:
                print(f"[SKIP] {e.name}: vượt size file dat")
                continue

            out_path = safe_output_path(out_dir, e.name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path = ensure_unique_path(out_path)

            with out_path.open("wb") as fout:
                copy_range(fin, e.offset, e.size, fout)

            print(f"[OK] {e.index:03d} {e.name} -> {out_path.relative_to(out_dir)} ({e.size} bytes)")

    return out_dir


def align_up(x: int, a: int) -> int:
    return (x + (a - 1)) & ~(a - 1) if a > 1 else x


def collect_files(folder: Path) -> List[Tuple[str, Path]]:
    items: List[Tuple[str, Path]] = []
    for p in folder.rglob("*"):
        if p.is_file():
            rel = p.relative_to(folder)
            arc_name = str(rel).replace("\\", "/")  # tên trong dat dùng /
            items.append((arc_name, p))
    items.sort(key=lambda t: t[0].lower())  # cố định thứ tự
    return items


def validate_name_bytes(name: str) -> bytes:
    b = name.encode("utf-8", "strict")
    if len(b) >= NAME_LEN:
        raise ValueError(f"Tên quá dài (max {NAME_LEN-1} bytes UTF-8): {name!r}")
    return b


def pack_folder(folder: Path) -> Path:
    if not folder.is_dir():
        raise ValueError("Input phải là thư mục.")

    files = collect_files(folder)
    if not files:
        raise ValueError("Thư mục rỗng (không có file).")

    out_dat = folder.with_name(folder.name + "_new.dat")

    n = len(files)
    table_size = ENTRY_SIZE * (n + 1)  # + terminator
    data_offset = HEADER_SIZE + table_size
    cur = data_offset

    table_entries: List[Tuple[bytes, int, int]] = []
    for arc_name, file_path in files:
        name_b = validate_name_bytes(arc_name)
        size = file_path.stat().st_size
        cur = align_up(cur, ALIGN)
        offset = cur
        table_entries.append((name_b, size, offset))
        cur = offset + size

    header = MAGIC_PREFIX + b"\x00" * (HEADER_SIZE - len(MAGIC_PREFIX))

    with out_dat.open("wb") as fout:
        fout.write(header)

        for name_b, size, offset in table_entries:
            rec = bytearray(ENTRY_SIZE)
            rec[:len(name_b)] = name_b
            rec[NAME_LEN:NAME_LEN + 8] = u64le(size)
            rec[NAME_LEN + 8:NAME_LEN + 16] = u64le(offset)
            fout.write(rec)

        fout.write(b"\x00" * ENTRY_SIZE)  # terminator

        for (arc_name, file_path), (_, size, offset) in zip(files, table_entries):
            fout.seek(offset)
            with file_path.open("rb") as fin:
                while True:
                    buf = fin.read(1024 * 1024)
                    if not buf:
                        break
                    fout.write(buf)
            print(f"[OK] pack {arc_name} ({size} bytes) @0x{offset:08X}")

    return out_dat


def menu() -> None:
    print("PS_FS_V1? (.dat) TOOL (RAW)")
    print("1: Unpack file dat  -> thư mục cùng tên")
    print("2: Pack thư mục     -> file dat cùng tên + _new")

    choice = input("Chọn (1/2): ").strip()
    if choice == "1":
        p = input("Nhập đường dẫn file .dat: ").strip().strip('"')
        dat_path = Path(p)
        if not dat_path.exists():
            print("Không tìm thấy file.")
            return
        out_dir = unpack_dat(dat_path)
        print(f"\nXong. Thư mục xuất: {out_dir}")
    elif choice == "2":
        p = input("Nhập đường dẫn thư mục muốn pack: ").strip().strip('"')
        folder = Path(p)
        if not folder.exists():
            print("Không tìm thấy thư mục.")
            return
        out_dat = pack_folder(folder)
        print(f"\nXong. File xuất: {out_dat}")
    else:
        print("Lựa chọn không hợp lệ.")


def main() -> None:
    # CLI optional:
    #   psfs_dat_tool.py unpack file.dat
    #   psfs_dat_tool.py pack   folder
    if len(sys.argv) >= 3:
        cmd = sys.argv[1].lower()
        target = Path(sys.argv[2])
        if cmd == "unpack":
            out_dir = unpack_dat(target)
            print(f"\nXong. Thư mục xuất: {out_dir}")
            return
        if cmd == "pack":
            out_dat = pack_folder(target)
            print(f"\nXong. File xuất: {out_dat}")
            return
        print("CLI: unpack <file.dat> | pack <folder>")
        sys.exit(1)

    menu()


if __name__ == "__main__":
    main()
