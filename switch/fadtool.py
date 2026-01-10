#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAD tool 2 chiều (gộp faddec + fadex) - RAW

1) Unpack .fad:
   - hỏi đường dẫn file .fad
   - decompress (YKCMP_V1 zlib)
   - unpack theo file table (HEADER_OFFSET=0x58, ENTRY_SIZE=0x20)
   - xuất ra thư mục cùng tên (bỏ .fad)

2) Pack .fad:
   - hỏi đường dẫn file .fad gốc + thư mục đã unpack/chỉnh sửa
   - build lại decompressed.bin (giữ nguyên header/table unknown, chỉ cập nhật size/offset)
   - pad mỗi file lên bội số 0x10 (để giữ alignment)
   - compress lại YKCMP_V1
   - xuất ra file cùng tên thêm _new.fad

Ghi chú:
- Script dựa trên logic bạn đã dùng:
  - decompress: payload mặc định 0x14; nếu fail sẽ scan zlib header 78 01/9C/DA
  - unpack: size @+0x00 (u32), offset @+0x08 (u32)
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

YKCMP_MAGIC = b"YKCMP_V1"

HEADER_OFFSET = 0x58
ENTRY_SIZE = 0x20
ALIGN = 0x10


@dataclass
class FadEntry:
    index: int
    table_pos: int
    offset: int
    size: int


def u32_le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def pack_u32_le(x: int) -> bytes:
    return struct.pack("<I", x)


def align_up(x: int, a: int) -> int:
    return (x + (a - 1)) & ~(a - 1) if a > 1 else x


def find_zlib_offset(data: bytes, start=0x10, end=0x2000) -> Optional[int]:
    end = min(len(data) - 2, end)
    for i in range(start, end):
        b0 = data[i]
        b1 = data[i + 1]
        if b0 == 0x78 and b1 in (0x01, 0x9C, 0xDA):
            return i
    return None


def ykcmp_decompress(fad_bytes: bytes) -> Tuple[int, int, int, bytes]:
    """
    Returns (flags, file_size_hdr, decomp_size_hdr, decompressed_bytes)
    """
    if len(fad_bytes) < 0x20:
        raise ValueError("File quá nhỏ.")
    if fad_bytes[:8] != YKCMP_MAGIC:
        raise ValueError("Magic không khớp (không phải YKCMP_V1).")

    flags = u32_le(fad_bytes, 0x08)
    file_size_hdr = u32_le(fad_bytes, 0x0C)
    decomp_size_hdr = u32_le(fad_bytes, 0x10)

    def try_decomp_at(off: int) -> Optional[bytes]:
        d = zlib.decompressobj()
        out = bytearray()
        pos = off
        chunk = 1 << 20
        while pos < len(fad_bytes):
            part = fad_bytes[pos:pos + chunk]
            pos += len(part)
            out += d.decompress(part)
            if decomp_size_hdr and len(out) >= decomp_size_hdr:
                break
        out += d.flush()
        if decomp_size_hdr and len(out) < decomp_size_hdr:
            # vẫn trả, nhưng cảnh báo ngoài
            return bytes(out)
        return bytes(out)

    # Default payload offset
    payload_off = 0x14
    try:
        dec = try_decomp_at(payload_off)
        # một số file vẫn decompress được dù offset sai -> check tối thiểu
        if dec and len(dec) > 0x100:
            return flags, file_size_hdr, decomp_size_hdr, dec
    except zlib.error:
        dec = None

    # Scan fallback
    zoff = find_zlib_offset(fad_bytes)
    if zoff is None:
        raise ValueError("Không tìm thấy zlib header (78 01/9C/DA) khi scan.")
    try:
        dec = try_decomp_at(zoff)
    except zlib.error as e:
        raise ValueError(f"Decompress fail tại 0x{zoff:X}: {e}") from e

    return flags, file_size_hdr, decomp_size_hdr, dec


def ykcmp_compress(dec_bytes: bytes, flags: int = 7, level: int = 9) -> bytes:
    zdata = zlib.compress(dec_bytes, level)
    file_size = 0x14 + len(zdata)  # 20 + compressed size
    decomp_size = len(dec_bytes)

    out = bytearray()
    out += YKCMP_MAGIC
    out += pack_u32_le(flags)
    out += pack_u32_le(file_size)
    out += pack_u32_le(decomp_size)
    out += zdata
    return bytes(out)


def parse_inner_archive(dec: bytes) -> List[FadEntry]:
    """
    Parse file table at HEADER_OFFSET with ENTRY_SIZE.
    Stop when:
      - entry is all zeros
      - size/offset invalid or out-of-range
    """
    entries: List[FadEntry] = []
    filesize = len(dec)

    idx = 0
    while True:
        pos = HEADER_OFFSET + idx * ENTRY_SIZE
        if pos + ENTRY_SIZE > filesize:
            break
        ent = dec[pos:pos + ENTRY_SIZE]
        if all(b == 0 for b in ent):
            break

        size = int.from_bytes(ent[0x00:0x04], "little")
        offset = int.from_bytes(ent[0x08:0x0C], "little")

        # stop if invalid
        if size <= 0 or offset <= 0:
            break
        if offset > filesize or offset + size > filesize:
            break

        entries.append(FadEntry(idx, pos, offset, size))
        idx += 1

    if not entries:
        raise ValueError("Không tìm thấy entry nào trong bảng file (check HEADER_OFFSET/ENTRY_SIZE).")

    return entries


def guess_digits(n: int) -> int:
    return max(2, len(str(max(0, n - 1))))


def unpack_fad(fad_path: Path) -> Path:
    fad_bytes = fad_path.read_bytes()
    flags, file_size_hdr, decomp_size_hdr, dec = ykcmp_decompress(fad_bytes)

    # Info
    print(f"[*] Input: {fad_path}")
    print(f"[*] Flags @0x08: {flags}")
    print(f"[*] Header file_size @0x0C: {file_size_hdr}  (real={len(fad_bytes)})")
    print(f"[*] Header decomp_size @0x10: {decomp_size_hdr} (real={len(dec)})")
    if decomp_size_hdr and len(dec) != decomp_size_hdr:
        print(f"[WARN] Decompressed size mismatch: hdr={decomp_size_hdr}, real={len(dec)}")

    entries = parse_inner_archive(dec)
    out_dir = fad_path.with_suffix("")  # folder cùng tên (bỏ .fad)
    out_dir.mkdir(parents=True, exist_ok=True)

    digits = guess_digits(len(entries))
    for e in entries:
        chunk = dec[e.offset:e.offset + e.size]
        out_name = f"file_{e.index:0{digits}d}.bin"
        out_path = out_dir / out_name
        out_path.write_bytes(chunk)
        print(f"[OK] {out_name}  off=0x{e.offset:X}  size=0x{e.size:X}")

    print(f"\nDONE. Out folder: {out_dir}")
    return out_dir


def collect_replacements(folder: Path) -> Dict[int, Path]:
    """
    Collect files like file_00.bin, file_01.bin, ... (any extension accepted)
    """
    mp: Dict[int, Path] = {}
    rx = re.compile(r"^file_(\d+)\.[^.]+$", re.IGNORECASE)
    for p in folder.iterdir():
        if not p.is_file():
            continue
        m = rx.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        # If duplicated, pick lexicographically smaller (stable)
        if idx not in mp or p.name < mp[idx].name:
            mp[idx] = p
    return mp


def pack_fad(original_fad: Path, folder: Path) -> Path:
    if not original_fad.exists():
        raise FileNotFoundError(original_fad)
    if not folder.is_dir():
        raise NotADirectoryError(folder)

    fad_bytes = original_fad.read_bytes()
    flags, _, decomp_size_hdr, dec0 = ykcmp_decompress(fad_bytes)
    entries = parse_inner_archive(dec0)

    rep = collect_replacements(folder)

    base_data_off = min(e.offset for e in entries)
    # Keep header+table+unknown bytes intact up to the first data offset
    header_block = bytearray(dec0[:base_data_off])

    cur = base_data_off
    data_out = bytearray()

    # Build new data in same order
    for e in entries:
        # load new data or fallback old data
        if e.index in rep:
            raw = rep[e.index].read_bytes()
        else:
            print(f"[WARN] Thiếu file_{e.index}.xxx trong folder -> giữ dữ liệu gốc.")
            raw = dec0[e.offset:e.offset + e.size]

        # pad to ALIGN
        if len(raw) % ALIGN != 0:
            raw += b"\x00" * (ALIGN - (len(raw) % ALIGN))

        cur = align_up(cur, ALIGN)
        new_off = cur
        new_size = len(raw)

        # patch table: size @+0x00, offset @+0x08
        table_pos = e.table_pos
        # Ensure header_block covers table_pos
        if table_pos + ENTRY_SIZE > len(header_block):
            raise ValueError("Header block không đủ dài để patch bảng (base_data_off quá nhỏ?).")

        header_block[table_pos + 0x00:table_pos + 0x04] = pack_u32_le(new_size)
        header_block[table_pos + 0x08:table_pos + 0x0C] = pack_u32_le(new_off)

        # append data with possible gap (shouldn't happen if contiguous)
        desired_data_pos = new_off - base_data_off
        if desired_data_pos < 0:
            raise ValueError("Offset tính ra âm (lỗi).")
        if desired_data_pos > len(data_out):
            data_out += b"\x00" * (desired_data_pos - len(data_out))

        data_out += raw
        cur = new_off + new_size

        print(f"[OK] pack idx={e.index}  off=0x{new_off:X}  size=0x{new_size:X}")

    dec_new = bytes(header_block) + bytes(data_out)

    if decomp_size_hdr and len(dec_new) != decomp_size_hdr:
        # Nhiều game không cần giữ decomp_size cũ, header YKCMP sẽ ghi size mới.
        print(f"[INFO] Decompressed size changed: old={decomp_size_hdr}, new={len(dec_new)}")

    fad_new = ykcmp_compress(dec_new, flags=flags, level=9)

    out_path = original_fad.with_name(original_fad.stem + "_new" + original_fad.suffix)
    out_path.write_bytes(fad_new)

    print(f"\nDONE. Wrote: {out_path}")
    return out_path


def menu():
    print("=== FAD TOOL (YKCMP_V1) 2 CHIỀU ===")
    print("1. Unpack fad file (decompress + unpack)")
    print("2. Pack fad file (folder -> compress)")

    c = input("Chọn (1/2): ").strip()
    if c == "1":
        p = input("Nhập đường dẫn file .fad: ").strip().strip('"').strip("'")
        if not p:
            return
        unpack_fad(Path(p))
    elif c == "2":
        fad = input("Nhập đường dẫn file .fad gốc: ").strip().strip('"').strip("'")
        folder = input("Nhập đường dẫn thư mục muốn pack: ").strip().strip('"').strip("'")
        if not fad or not folder:
            return
        pack_fad(Path(fad), Path(folder))
    else:
        print("Lựa chọn không hợp lệ.")


def main():
    # Optional CLI:
    #   fad_tool_2way.py unpack path/to/file.fad
    #   fad_tool_2way.py pack path/to/file.fad path/to/folder
    if len(sys.argv) >= 3:
        cmd = sys.argv[1].lower()
        if cmd == "unpack":
            unpack_fad(Path(sys.argv[2].strip('"')))
            return
        if cmd == "pack":
            if len(sys.argv) < 4:
                print("Usage: pack <file.fad> <folder>")
                sys.exit(1)
            pack_fad(Path(sys.argv[2].strip('"')), Path(sys.argv[3].strip('"')))
            return

    menu()


if __name__ == "__main__":
    main()
