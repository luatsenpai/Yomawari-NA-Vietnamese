#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
import zlib
from pathlib import Path


def u32_le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def find_zlib_offset(data: bytes, start=0, end=None) -> int | None:
    """
    Find likely zlib header bytes: 78 01 / 78 9C / 78 DA
    """
    if end is None:
        end = len(data) - 2
    for i in range(start, max(start, end)):
        b0 = data[i]
        b1 = data[i + 1]
        if b0 == 0x78 and b1 in (0x01, 0x9C, 0xDA):
            return i
    return None


def decompress_stream_to_file(payload: bytes, out_path: Path, expected_size: int | None = None, chunk_in=1 << 20):
    """
    Streaming zlib decompression to file.
    payload: bytes starting at zlib stream.
    """
    d = zlib.decompressobj()
    written = 0

    with out_path.open("wb") as f:
        pos = 0
        n = len(payload)

        while pos < n:
            part = payload[pos:pos + chunk_in]
            pos += len(part)

            out = d.decompress(part)
            if out:
                f.write(out)
                written += len(out)

            # optional: early stop if we already reached expected size
            if expected_size is not None and written >= expected_size:
                # still need to flush to keep zlib state consistent
                break

        # Flush remaining output from decompressor
        tail = d.flush()
        if tail:
            f.write(tail)
            written += len(tail)

    return written


def main():
    print("=== YKCMP_V1 .fad Decompress (stream) ===\n")

    fad_in = input("Nhập đường dẫn file .fad: ").strip().strip('"').strip("'")
    if not fad_in:
        print("Chưa nhập đường dẫn. Thoát.")
        return

    fad_path = Path(fad_in)
    if not fad_path.exists():
        print("Không tìm thấy file:", fad_path)
        return

    out_dir_in = input("Thư mục output (Enter = cùng thư mục): ").strip().strip('"').strip("'")
    out_dir = Path(out_dir_in) if out_dir_in else fad_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    data = fad_path.read_bytes()
    if len(data) < 0x20:
        print("File quá nhỏ, không hợp lệ.")
        return

    if data[:8] != b"YKCMP_V1":
        print("Magic không khớp (không phải YKCMP_V1).")
        return

    file_size_hdr = u32_le(data, 0x0C)
    decomp_size_hdr = u32_le(data, 0x10)

    print(f"[INFO] Input size: {len(data)} bytes")
    print(f"[INFO] Header file_size @0x0C: {file_size_hdr} (0x{file_size_hdr:X})")
    print(f"[INFO] Header decomp_size @0x10: {decomp_size_hdr} (0x{decomp_size_hdr:X})")

    # Default payload offset (đúng với file bạn đưa)
    payload_off = 0x14

    # Try decompress at 0x14 first; if fails, scan for zlib header
    out_path = out_dir / f"{fad_path.stem}_decompressed.bin"

    def try_decompress_at(off: int) -> bool:
        nonlocal out_path
        try:
            written = decompress_stream_to_file(data[off:], out_path, expected_size=decomp_size_hdr)
            print(f"[OK] Decompressed from 0x{off:X} -> wrote {written} bytes")
            return True
        except zlib.error as e:
            print(f"[WARN] zlib error at 0x{off:X}: {e}")
            return False

    ok = try_decompress_at(payload_off)

    if not ok:
        print("[INFO] Scanning for zlib header...")
        found = find_zlib_offset(data, start=0x10, end=min(len(data), 0x2000))
        if found is None:
            print("[ERROR] Không tìm thấy zlib header (78 01/9C/DA) trong vùng scan.")
            return
        print(f"[INFO] Found likely zlib header at 0x{found:X}")
        ok = try_decompress_at(found)
        if not ok:
            print("[ERROR] Decompress vẫn fail sau khi tìm offset zlib.")
            return

    # Verify output size
    real_size = out_path.stat().st_size
    if real_size != decomp_size_hdr:
        print(f"[WARN] Output size ({real_size}) != header decomp_size ({decomp_size_hdr}).")
        print("       Nếu vẫn parse được archive thì có thể header sai, nhưng thường là bạn đang thiếu/chọn sai offset.")
    else:
        print("[OK] Output size matches header decomp_size.")

    print("\nWrote:", out_path)


if __name__ == "__main__":
    main()
