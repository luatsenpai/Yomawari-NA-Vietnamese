#!/usr/bin/env python3
import os
import sys

MAGIC = b"YKCMP_V1"


def parse_offset(s: str) -> int:
    """
    Cho phép: "0x30", "30", "30h"
    """
    s = s.strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    if s.endswith("h"):
        return int(s[:-1], 16)
    # int(..., 0) cho phép "0", "123", "0x30" (nhưng mình đã xử lý 0x rồi)
    return int(s, 0)


def ykcmp_decompress_from(data: bytes, offset: int) -> bytes:
    """
    Giải nén 1 block YKCMP_V1 nằm trong 'data' tại vị trí 'offset' (bắt đầu bằng chữ Y của YKCMP_V1).
    Trả về: bytes giải nén (không kèm header YKCMP).
    """

    if offset < 0 or offset + 20 > len(data):
        raise ValueError("Offset YKCMP nằm ngoài file hoặc header không đủ 20 byte")

    if data[offset:offset + 8] != MAGIC:
        raise ValueError("Không thấy magic 'YKCMP_V1' tại offset đã cho")

    # Header:
    # +0x00: "YKCMP_V1" (8)
    # +0x08: version (4) - thường là 4
    # +0x0C: zsize = kích thước toàn bộ archive (header + compressed)
    # +0x10: dsize = kích thước sau giải nén
    version = int.from_bytes(data[offset + 8:offset + 12], "little")
    zsize = int.from_bytes(data[offset + 12:offset + 16], "little")
    dsize = int.from_bytes(data[offset + 16:offset + 20], "little")

    if version != 4:
        # Với Yomawari / NIS gần như luôn = 4; báo cho dễ debug
        print(f"[!] Cảnh báo: ARCHIVE_VERSION = {version}, không phải 4 (vẫn thử giải nén).")

    if zsize <= 0:
        raise ValueError(f"zsize không hợp lệ: {zsize}")

    # archive nằm từ offset .. offset+zsize
    end_archive = offset + zsize
    if end_archive > len(data):
        raise ValueError("zsize vượt quá kích thước file")

    # Compressed data bắt đầu sau header 0x14
    comp_start = offset + 0x14
    comp_end = end_archive
    comp = data[comp_start:comp_end]
    comp_len = len(comp)

    # Buffer output
    out = bytearray(dsize)

    cp = 0  # pointer trong dữ liệu nén (comp)
    dp = 0  # pointer trong dữ liệu giải nén (out)

    while cp < comp_len and dp < dsize:
        a = comp[cp]

        # 0 = nop
        if a == 0:
            cp += 1
            continue

        # < 0x80: literal copy
        if a < 0x80:
            num = a
            cp += 1
            # copy trực tiếp num byte tiếp theo
            for _ in range(num):
                if cp >= comp_len or dp >= dsize:
                    break
                out[dp] = comp[cp]
                dp += 1
                cp += 1
            continue

        # >= 0x80: copy lookback
        # Tính READLEN & SEEKBACK theo đúng QuickBMS script (YKCMP_V1)
        if a >= 0xE0:
            # 3-byte sequence: A B C
            if cp + 2 >= comp_len:
                break
            b = comp[cp + 1]
            c = comp[cp + 2]

            # length = ((A & 0x1F) << 4) + (B >> 4) + 3
            readlen = a & 0x1F
            readlen = (readlen << 4)
            readlen += (b >> 4)
            readlen += 3

            # back = ((B & 0x0F) << 8) + C + 1
            seekback = b & 0x0F
            seekback = (seekback << 8) + c
            seekback += 1

            cp += 3

        elif a >= 0xC0:
            # 2-byte sequence: A B
            if cp + 1 >= comp_len:
                break
            b = comp[cp + 1]

            # length = (A & 0x3F) + 2
            readlen = (a & 0x3F) + 2

            # back = B + 1
            seekback = b + 1

            cp += 2

        else:
            # 0x80 <= a < 0xC0, 1-byte sequence: A
            # READLEN: lấy 2 bit cao của (A >> 4) & 3, rồi +1
            readlen = (a >> 4) & 0x03
            readlen += 1

            # back: (A & 0x0F) + 1
            seekback = (a & 0x0F) + 1

            cp += 1

        # Thực hiện copy từ out[dp - seekback] length lần
        for _ in range(readlen):
            if dp >= dsize:
                break
            src_pos = dp - seekback
            if src_pos < 0:
                # Không nên xảy ra, nhưng đề phòng
                val = 0
            else:
                val = out[src_pos]
            out[dp] = val
            dp += 1

    if dp != dsize:
        print(f"[!] Cảnh báo: giải nén được {dp} / {dsize} byte (không đủ).")

    return bytes(out)


def main():
    if len(sys.argv) != 3:
        print("Cách dùng:")
        print(f"  {sys.argv[0]} offset input_file")
        print()
        print("Ví dụ:")
        print(f"  {sys.argv[0]} 0x30 ch6010jp_decompressed.bin")
        print(f"  {sys.argv[0]} 48 ch6010_decompressed.bin")
        sys.exit(1)

    off_str = sys.argv[1]
    in_path = sys.argv[2]

    try:
        offset = parse_offset(off_str)
    except ValueError:
        print(f"Lỗi: offset không hợp lệ: {off_str}")
        sys.exit(1)

    if not os.path.isfile(in_path):
        print(f"Lỗi: không tìm thấy file: {in_path}")
        sys.exit(1)

    with open(in_path, "rb") as f:
        data = f.read()

    print(f"[*] File: {in_path}")
    print(f"[*] Kích thước: {len(data)} bytes")
    print(f"[*] Offset YKCMP_V1: 0x{offset:X}")

    try:
        dec = ykcmp_decompress_from(data, offset)
    except Exception as e:
        print(f"[!] Lỗi khi giải nén: {e}")
        sys.exit(1)

    out_path = in_path + "_dec"
    with open(out_path, "wb") as f:
        f.write(dec)

    print(f"[+] Đã ghi dữ liệu giải nén (không kèm header YKCMP) vào: {out_path}")
    print(f"[+] Kích thước output: {len(dec)} bytes")


if __name__ == "__main__":
    main()
