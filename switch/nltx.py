# -*- coding: utf-8 -*-
import struct
import zlib
import math
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None


def count_lsb_zeros(value: int) -> int:
    c = 0
    while c < 32 and ((value >> c) & 1) == 0:
        c += 1
    return c


class Swizzler:
    def __init__(self, width: int, bpp: int, block_height: int):
        # bpp = bytes per pixel (1 = L8, 3 = RGB8, 4 = RGBA8)
        self.bhMask = (block_height * 8) - 1
        self.bhShift = count_lsb_zeros(block_height * 8)
        self.bppShift = count_lsb_zeros(bpp)
        widthInGobs = math.ceil(width * bpp / 64.0)
        self.gobStride = 512 * block_height * widthInGobs
        self.xShift = count_lsb_zeros(block_height * 512)

    def get_offset(self, x: int, y: int) -> int:
        # Trả về offset (theo byte) trong buffer swizzled tương ứng pixel (x,y)
        x <<= self.bppShift
        off = (y >> self.bhShift) * self.gobStride
        off += (x >> 6) << self.xShift
        off += ((y & self.bhMask) >> 3) << 9
        off += ((x & 0x3F) >> 5) << 8
        off += ((y & 0x07) >> 1) << 6
        off += ((x & 0x1F) >> 4) << 5
        off += ((y & 0x01) >> 0) << 4
        off += (x & 0x0F)
        return off


def find_ykcmp_offset(data: bytes) -> int:
    off = data.find(b"YKCMP_V1")
    if off < 0:
        raise ValueError("Không tìm thấy magic 'YKCMP_V1' trong file.")
    return off


def read_nmpltex_header(data: bytes):
    if not data.startswith(b"NMPLTEX1"):
        raise ValueError("Không phải file NMPLTEX1 (.nltx).")
    # Dạng raw .nltx của Yomawari: width, height ở 0x18/0x1C
    width = struct.unpack_from("<I", data, 0x18)[0]
    height = struct.unpack_from("<I", data, 0x1C)[0]
    flags = struct.unpack_from("<I", data, 0x30)[0]
    return width, height, flags


def get_block_height(flags: int) -> int:
    # Thấp 4 bit cuối chứa exponent block_height (ví dụ 0x205 -> 5 -> 2^5 = 32)
    bh_exp = flags & 0xF
    if bh_exp < 0 or bh_exp > 7:
        bh_exp = 5  # fallback an toàn: 2^5 = 32
    return 1 << bh_exp


def ykcmp_decompress(data: bytes, yk_off: int):
    yk_type, zsize, dsize = struct.unpack_from("<III", data, yk_off + 8)
    comp_start = yk_off + 0x14

    # Cắt theo zsize; nếu header "phóng đại" thì chỉ lấy đến hết file
    comp_end = min(len(data), comp_start + zsize)
    comp_data = data[comp_start:comp_end]
    if not comp_data:
        raise ValueError("Không tìm thấy dữ liệu nén YKCMP.")

    raw = zlib.decompress(comp_data)
    if len(raw) < dsize:
        raise ValueError("Dữ liệu sau zlib ngắn hơn dsize trong header.")
    if len(raw) > dsize:
        raw = raw[:dsize]
    return yk_type, zsize, dsize, comp_start, raw


# ========== NLTX -> PNG ==========

def nltx_to_png(nltx_path: Path):
    if Image is None:
        raise RuntimeError("Thiếu thư viện Pillow. Cài bằng: pip install pillow")

    data = nltx_path.read_bytes()
    width, height, flags = read_nmpltex_header(data)
    yk_off = find_ykcmp_offset(data)
    yk_type, zsize, dsize, comp_start, swizzled = ykcmp_decompress(data, yk_off)

    pixels = width * height
    if dsize % pixels != 0:
        raise ValueError("dsize không chia hết cho width*height, không rõ bpp.")
    bpp = dsize // pixels

    block_height = get_block_height(flags)
    sw = Swizzler(width, bpp, block_height)

    linear = bytearray(pixels * bpp)
    for y in range(height):
        for x in range(width):
            dst_off = (y * width + x) * bpp
            src_off = sw.get_offset(x, y)
            if src_off + bpp <= len(swizzled):
                linear[dst_off:dst_off + bpp] = swizzled[src_off:src_off + bpp]

    if bpp == 1:
        mode = "L"       # grayscale
    elif bpp == 3:
        mode = "RGB"
    elif bpp == 4:
        mode = "RGBA"    # RGB + alpha
    else:
        raise ValueError(f"bpp = {bpp} không được hỗ trợ (chỉ hỗ trợ 1,3,4).")

    img = Image.frombytes(mode, (width, height), bytes(linear))
    out_path = nltx_path.with_suffix(".png")
    img.save(out_path)
    return out_path


# ========== PNG -> NLTX ==========

def png_to_nltx(png_path: Path):
    if Image is None:
        raise RuntimeError("Thiếu thư viện Pillow. Cài bằng: pip install pillow")

    # Template .nltx cùng tên, cùng thư mục
    nltx_path = png_path.with_suffix(".nltx")
    if not nltx_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file template .nltx cùng tên: {nltx_path}"
        )

    data = nltx_path.read_bytes()
    width, height, flags = read_nmpltex_header(data)
    yk_off = find_ykcmp_offset(data)
    yk_type, zsize_old, dsize_old, comp_start, _ = ykcmp_decompress(data, yk_off)

    pixels = width * height
    if dsize_old % pixels != 0:
        raise ValueError("dsize không chia hết cho width*height, không rõ bpp.")
    bpp = dsize_old // pixels

    img = Image.open(png_path)
    if img.size != (width, height):
        raise ValueError(
            f"Kích thước PNG {img.size} khác với NLTX ({width}x{height}). "
            f"Hãy resize/crop trước cho khớp."
        )

    # Chuyển về đúng format theo bpp của texture gốc
    if bpp == 1:
        img = img.convert("L")
    elif bpp == 3:
        img = img.convert("RGB")
    elif bpp == 4:
        # hỗ trợ ảnh RGB + alpha
        img = img.convert("RGBA")
    else:
        raise ValueError(f"bpp = {bpp} không được hỗ trợ (chỉ hỗ trợ 1,3,4).")

    linear = img.tobytes()
    if len(linear) != dsize_old:
        raise ValueError(
            f"Số byte ảnh ({len(linear)}) khác dsize ({dsize_old}). "
            "Có thể sai bpp hoặc format."
        )

    block_height = get_block_height(flags)
    sw = Swizzler(width, bpp, block_height)
    swizzled = bytearray(dsize_old)
    for y in range(height):
        for x in range(width):
            src_off = (y * width + x) * bpp
            dst_off = sw.get_offset(x, y)
            if dst_off + bpp <= len(swizzled):
                swizzled[dst_off:dst_off + bpp] = linear[src_off:src_off + bpp]

    # Nén lại bằng zlib, type 7 giữ nguyên
    zdata = zlib.compress(bytes(swizzled))

    # Dựng lại file NLTX mới: giữ nguyên mọi thứ trừ 3 field type/zsize/dsize + payload
    header_until_type = data[:yk_off + 8]
    _, _, dsize_template = struct.unpack_from("<III", data, yk_off + 8)
    yk_header = struct.pack("<III", yk_type, len(zdata), dsize_template)

    old_comp_end = min(len(data), comp_start + zsize_old)
    trailing = data[old_comp_end:]

    new_data = bytearray()
    new_data += header_until_type
    new_data += yk_header
    new_data += zdata
    new_data += trailing

    out_path = png_path.with_suffix(".nltx")
    out_path.write_bytes(new_data)
    return out_path


# ========== CLI ==========

def main():
    print("=== NLTX <-> PNG tool (YKCMP_V1 / NMPLTEX1) ===")
    print("1) NLTX -> PNG")
    print("2) PNG  -> NLTX (dùng template .nltx cùng tên)")
    choice = input("Chọn (1/2): ").strip()

    try:
        if choice == "1":
            path_str = input("Đường dẫn file .nltx: ").strip().strip('\"')
            nltx_path = Path(path_str)
            if not nltx_path.exists():
                print("Không tìm thấy file:", nltx_path)
                return
            out = nltx_to_png(nltx_path)
            print("Đã xuất PNG:", out)
        elif choice == "2":
            path_str = input("Đường dẫn file .png: ").strip().strip('\"')
            png_path = Path(path_str)
            if not png_path.exists():
                print("Không tìm thấy file:", png_path)
                return
            out = png_to_nltx(png_path)
            print("Đã ghi lại NLTX:", out)
        else:
            print("Lựa chọn không hợp lệ.")
    except Exception as e:
        print("Lỗi:", e)


if __name__ == "__main__":
    main()
