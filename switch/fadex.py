#!/usr/bin/env python3
import os
import sys

# Offset bắt đầu bảng file
HEADER_OFFSET = 0x58
# Kích thước mỗi entry trong bảng
ENTRY_SIZE = 0x20


def read_entries(f, filesize):
    """
    Đọc bảng file table từ HEADER_OFFSET.
    Trả về list các tuple (index, offset, size).
    """
    entries = []
    index = 0

    while True:
        entry_pos = HEADER_OFFSET + index * ENTRY_SIZE
        if entry_pos + ENTRY_SIZE > filesize:
            break  # ra ngoài file rồi

        f.seek(entry_pos)
        entry = f.read(ENTRY_SIZE)
        if len(entry) < ENTRY_SIZE:
            break

        # Nếu cả entry đều là 0 -> coi như hết bảng
        if all(b == 0 for b in entry):
            break

        # Theo mô tả & thực tế:
        # size: 4 byte LE tại offset +0x00
        # offset: 4 byte LE tại offset +0x08
        size = int.from_bytes(entry[0x00:0x04], "little")
        offset = int.from_bytes(entry[0x08:0x0C], "little")

        # Có thể dùng 3 byte như bạn mô tả:
        # size = int.from_bytes(entry[0x00:0x03], "little")
        # offset = int.from_bytes(entry[0x08:0x0B], "little")

        # Điều kiện dừng nếu dữ liệu không hợp lệ
        if size <= 0 or offset <= 0:
            break
        if offset > filesize or offset + size > filesize:
            break

        entries.append((index, offset, size))
        index += 1

    return entries


def unpack_archive(path, outdir=None):
    if not os.path.isfile(path):
        print(f"Không tìm thấy file: {path}")
        return

    filesize = os.path.getsize(path)
    if outdir is None:
        outdir = path + "_unpacked"

    os.makedirs(outdir, exist_ok=True)

    with open(path, "rb") as f:
        entries = read_entries(f, filesize)

        print(f"[*] File: {path}")
        print(f"[*] Kích thước: {filesize} bytes")
        print(f"[*] Số file con tìm được: {len(entries)}\n")

        for index, offset, size in entries:
            f.seek(offset)
            data = f.read(size)

            # Đặt tên file: file_00.bin, file_01.bin, ...
            out_name = f"file_{index:02d}.bin"
            out_path = os.path.join(outdir, out_name)

            with open(out_path, "wb") as out_f:
                out_f.write(data)

            print(f"[+] Xuất {out_name}: offset=0x{offset:X}, size={size} bytes")


def main():
    if len(sys.argv) < 2:
        print("Cách dùng:")
        print(f"  {sys.argv[0]} <file_archive> [thư_mục_output]")
        print()
        print("Ví dụ:")
        print(f"  {sys.argv[0]} ch6010_decompressed.bin")
        print(f"  {sys.argv[0]} ch6010jp_decompressed.bin out_ch6010jp")
        return

    in_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) >= 3 else None
    unpack_archive(in_path, out_dir)


if __name__ == "__main__":
    main()
