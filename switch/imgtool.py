# tex_tool_2way.py
import struct
from pathlib import Path

from PIL import Image, ImageChops

# =========================
# Helpers: swizzle (Vita-like)
# =========================

def part1by1_16(n: int) -> int:
    n &= 0xFFFF
    n = (n | (n << 8)) & 0x00FF00FF
    n = (n | (n << 4)) & 0x0F0F0F0F
    n = (n | (n << 2)) & 0x33333333
    n = (n | (n << 1)) & 0x55555555
    return n

def morton2(x: int, y: int) -> int:
    return part1by1_16(x) | (part1by1_16(y) << 1)

def unswizzle_morton_blocks(raw: bytes, w: int, h: int, bpp: int, bw: int = 8, bh: int = 8) -> bytes:
    # blocks in Morton order, pixels inside block are linear
    block_bytes = bw * bh * bpp
    out = bytearray(w * h * bpp)
    mv = memoryview(raw)
    ov = memoryview(out)

    for y in range(h):
        by = y // bh
        iy = y % bh
        row_off = y * w * bpp
        for x in range(w):
            bx = x // bw
            ix = x % bw
            bi = morton2(bx, by)
            src = bi * block_bytes + (iy * bw + ix) * bpp
            dst = row_off + x * bpp
            if src + bpp <= len(mv):
                ov[dst:dst + bpp] = mv[src:src + bpp]
            else:
                ov[dst:dst + bpp] = b"\x00" * bpp
    return bytes(out)

def swizzle_morton_blocks(linear: bytes, w: int, h: int, bpp: int, bw: int = 8, bh: int = 8) -> bytes:
    # inverse of unswizzle_morton_blocks
    block_bytes = bw * bh * bpp
    out = bytearray(w * h * bpp)
    mv = memoryview(linear)
    ov = memoryview(out)

    for y in range(h):
        by = y // bh
        iy = y % bh
        row_off = y * w * bpp
        for x in range(w):
            bx = x // bw
            ix = x % bw
            bi = morton2(bx, by)
            dst = bi * block_bytes + (iy * bw + ix) * bpp
            src = row_off + x * bpp
            if dst + bpp <= len(ov):
                ov[dst:dst + bpp] = mv[src:src + bpp]
    return bytes(out)

# =========================
# Helpers: format/parse
# =========================

DATA_OFF = 0x30

def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]

def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]

def parse_bin(buf: bytes) -> dict:
    file_size = u32(buf, 0x08)
    data_size = u32(buf, 0x10)   # chuẩn với các file bạn gửi: file_size - 0x30
    w = u16(buf, 0x18)
    h = u16(buf, 0x1A)
    fmt = u16(buf, 0x1E)
    flags = u32(buf, 0x28)

    if DATA_OFF + data_size > len(buf):
        raise ValueError("data_size vượt quá file (bin không đúng/thiếu dữ liệu).")

    data = buf[DATA_OFF:DATA_OFF + data_size]
    return dict(file_size=file_size, data_size=data_size, w=w, h=h, fmt=fmt, flags=flags, data=data)

def load_png_rgba(path: Path, w: int, h: int) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    if img.size != (w, h):
        # auto resize để khỏi fail (giữ nét kiểu game/atlas)
        img = img.resize((w, h), Image.NEAREST)
    return img

def rgba_to_mask_rgb(img_rgba: Image.Image) -> Image.Image:
    """
    PNG -> format mask gốc:
    - intensity lấy từ ALPHA
    - RGB = intensity, A=255
    """
    img = img_rgba.convert("RGBA")
    a = img.split()[3]                  # L
    full = Image.new("L", img.size, 255)
    return Image.merge("RGBA", (a, a, a, full))

def mask_rgb_to_transparent(img_rgba: Image.Image) -> Image.Image:
    """
    Bin -> PNG trong suốt:
    - alpha = MAX(R,G,B)
    - RGB = trắng
    """
    img = img_rgba.convert("RGBA")
    r, g, b, a = img.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    white = Image.new("L", img.size, 255)
    return Image.merge("RGBA", (white, white, white, mx))

def is_mask_texture(info: dict, base_raw_32: bytes) -> bool:
    """
    Heuristic theo format bạn gặp:
    - fmt=288 (0x120) và flags=0x304
    - alpha trong data thường = 255
    => coi là mask texture (alpha nằm trong RGB)
    """
    if info["fmt"] != 288:
        return False
    if info["flags"] != 0x304:
        return False
    # check alpha constant 255 (sample nhanh)
    mv = memoryview(base_raw_32)
    # sample ~4096 pixels
    step = max(4, (len(base_raw_32) // (4096 * 4)) * 4)
    for i in range(3, len(base_raw_32), step):
        if mv[i] != 255:
            return False
    return True

# =========================
# P8 encode/decode (palette RGBA + index)
# =========================

def decode_p8(pal_rgba: bytes, idx: bytes, w: int, h: int) -> Image.Image:
    pal = [pal_rgba[i:i+4] for i in range(0, 0x400, 4)]
    out = bytearray(w * h * 4)
    ov = memoryview(out)
    for i, pi in enumerate(idx):
        j = i * 4
        ov[j:j+4] = pal[pi]
    return Image.frombytes("RGBA", (w, h), bytes(out))

def encode_p8_from_png(img_rgba: Image.Image) -> tuple[bytes, bytes]:
    """
    PNG RGBA -> (palette_rgba[0x400], index[w*h])

    Robust alpha support (fix "game không đọc ảnh trong suốt"):
    - Nếu unique RGBA <= 256: giữ palette RGBA *đúng y chang*.
    - Nếu >256:
        * Quantize nhưng đảm bảo pixel alpha=0 được tách ra palette riêng (alpha=0),
          tránh bị "dính" chung với màu đen/biên dẫn tới nền bị đục.
        * Đồng thời nhét alpha bucket (0..15) vào low-nibble của kênh B lúc quantize
          để giảm trộn giữa semi-transparent và opaque.

    Palette cuối cùng KHÔNG lấy trực tiếp từ palette của Pillow (chỉ dùng để ra index),
    mà tính trung bình RGBA của pixel gốc theo từng index -> giữ alpha đúng hơn.
    """
    img = img_rgba.convert("RGBA")
    w, h = img.size

    raw = img.tobytes()
    mv = memoryview(raw)

    # 1) Try exact palette if <=256 unique RGBA
    uniq = {}
    colors = []
    for i in range(0, len(raw), 4):
        c = bytes(mv[i:i+4])
        if c not in uniq:
            uniq[c] = len(colors)
            colors.append(c)
            if len(colors) > 256:
                break

    if len(colors) <= 256:
        pal = bytearray(0x400)
        for i, c in enumerate(colors):
            pal[i*4:i*4+4] = c
        idx = bytearray(w * h)
        for p in range(w * h):
            c = bytes(mv[p*4:p*4+4])
            idx[p] = uniq[c]
        return bytes(pal), bytes(idx)

    # 2) Quantize with alpha-aware tricks
    # pick a marker RGB for fully transparent pixels (alpha==0) that is not used by opaque pixels
    candidates = [
        (255, 0, 255), (0, 255, 255), (255, 255, 0),
        (0, 255, 0), (255, 0, 0), (0, 0, 255),
        (255, 255, 255), (0, 0, 0),
    ]
    opaque_rgbs = set()
    step = max(1, (w * h) // 20000)  # sample up to ~20k pixels
    for p in range(0, w*h, step):
        a = mv[p*4 + 3]
        if a:
            opaque_rgbs.add((mv[p*4 + 0], mv[p*4 + 1], mv[p*4 + 2]))
            if len(opaque_rgbs) > 50000:
                break
    marker = (1, 0, 1)
    for c in candidates:
        if c not in opaque_rgbs:
            marker = c
            break

    # Build RGB image for quantize:
    # - alpha==0 -> marker color
    # - else -> original RGB but put alpha bucket (0..15) into low nibble of B
    rgb_bytes = bytearray(w * h * 3)
    ri = 0
    for p in range(w * h):
        r = mv[p*4 + 0]
        g = mv[p*4 + 1]
        b = mv[p*4 + 2]
        a = mv[p*4 + 3]
        if a == 0:
            rgb_bytes[ri:ri+3] = bytes(marker)
        else:
            b_mod = (b & 0xF0) | (a >> 4)
            rgb_bytes[ri:ri+3] = bytes((r, g, b_mod))
        ri += 3

    rgb_img = Image.frombytes("RGB", (w, h), bytes(rgb_bytes))
    pimg = rgb_img.quantize(colors=256, method=Image.MEDIANCUT)
    idx = pimg.tobytes()  # w*h

    # Build palette by averaging ORIGINAL RGBA per index
    sums_r = [0] * 256
    sums_g = [0] * 256
    sums_b = [0] * 256
    sums_a = [0] * 256
    cnts   = [0] * 256
    cnt_a0 = [0] * 256

    for p, pi in enumerate(idx):
        r = mv[p*4 + 0]
        g = mv[p*4 + 1]
        b = mv[p*4 + 2]
        a = mv[p*4 + 3]
        sums_r[pi] += r
        sums_g[pi] += g
        sums_b[pi] += b
        sums_a[pi] += a
        cnts[pi] += 1
        if a == 0:
            cnt_a0[pi] += 1

    pal = bytearray(0x400)
    for i in range(256):
        if cnts[i] == 0:
            pal[i*4:i*4+4] = b"\x00\x00\x00\x00"
            continue

        # If this index is fully transparent -> force alpha 0 and neutral RGB
        if cnt_a0[i] == cnts[i]:
            pal[i*4:i*4+4] = b"\x00\x00\x00\x00"
            continue

        r = sums_r[i] // cnts[i]
        g = sums_g[i] // cnts[i]
        b = sums_b[i] // cnts[i]
        a = sums_a[i] // cnts[i]
        pal[i*4:i*4+4] = bytes((r, g, b, a))

    return bytes(pal), idx

# =========================
# Main operations
# =========================

def bin_to_png(bin_path: Path):
    buf = bin_path.read_bytes()
    info = parse_bin(buf)
    w, h = info["w"], info["h"]
    data = info["data"]
    data_size = info["data_size"]

    out_png = bin_path.with_suffix(".png")

    # Case P8: fmt=8, data layout = [0x400 palette][w*h indices]
    if info["fmt"] == 8 and data_size >= 0x400 + (w*h):
        pal = data[:0x400]
        idx = data[0x400:0x400 + (w*h)]
        img = decode_p8(pal, idx, w, h)
        img.save(out_png)
        print(f"[OK] BIN->PNG (P8)  {bin_path.name} -> {out_png.name}")
        return

    # Case 32bpp: assume base level w*h*4
    base = w * h * 4
    if data_size >= base:
        raw32 = data[:base]
        # unswizzle if flags==0x304 (auto)
        if info["flags"] == 0x304:
            raw32_lin = unswizzle_morton_blocks(raw32, w, h, 4, 8, 8)
        else:
            raw32_lin = raw32

        img = Image.frombytes("RGBA", (w, h), raw32_lin)

        # mask-type -> export transparent PNG (RGB->Alpha)
        if is_mask_texture(info, raw32):
            img = mask_rgb_to_transparent(img)

        img.save(out_png)
        print(f"[OK] BIN->PNG (32bpp) {bin_path.name} -> {out_png.name}")
        return

    raise ValueError("Không nhận dạng được format để export PNG.")

def png_to_bin(bin_path: Path, png_path: Path):
    buf = bytearray(bin_path.read_bytes())
    info = parse_bin(buf)
    w, h = info["w"], info["h"]
    data_size = info["data_size"]
    data = info["data"]

    out_bin = bin_path.with_name(bin_path.stem + "_new" + bin_path.suffix)

    # P8
    if info["fmt"] == 8 and data_size >= 0x400 + (w*h):
        img = load_png_rgba(png_path, w, h)
        pal, idx = encode_p8_from_png(img)

        base_size = 0x400 + (w*h)
        new_data = bytearray(data)
        new_data[0:0x400] = pal
        new_data[0x400:0x400 + (w*h)] = idx
        # giữ nguyên tail nếu có mip/extra
        buf[DATA_OFF:DATA_OFF + data_size] = new_data

        out_bin.write_bytes(buf)
        print(f"[OK] PNG->BIN (P8)  {png_path.name} -> {out_bin.name}")
        return

    # 32bpp
    base = w * h * 4
    if data_size >= base:
        raw32 = data[:base]
        mask_mode = is_mask_texture(info, raw32)

        img = load_png_rgba(png_path, w, h)

        if mask_mode:
            # PNG alpha -> RGB, A=255
            img = rgba_to_mask_rgb(img)

        raw_lin = img.tobytes()  # RGBA linear

        # swizzle back if needed
        if info["flags"] == 0x304:
            raw_out = swizzle_morton_blocks(raw_lin, w, h, 4, 8, 8)
        else:
            raw_out = raw_lin

        new_data = bytearray(data)
        new_data[:base] = raw_out
        buf[DATA_OFF:DATA_OFF + data_size] = new_data

        out_bin.write_bytes(buf)
        print(f"[OK] PNG->BIN (32bpp) {png_path.name} -> {out_bin.name}")
        return

    raise ValueError("Không nhận dạng được format để import PNG.")

# =========================
# Interactive UI
# =========================

def ask_path(prompt: str) -> Path:
    s = input(prompt).strip().strip('"').strip("'")
    return Path(s)

def main():
    print("=== TEX TOOL 2 CHIỀU ===")
    print("1) Bin > PNG")
    print("2) PNG > Bin")
    choice = input("Chọn (1/2): ").strip()

    try:
        if choice == "1":
            bin_path = ask_path("Nhập đường dẫn file BIN: ")
            if not bin_path.exists():
                print("[ERR] Không thấy file BIN.")
                return
            bin_to_png(bin_path)

        elif choice == "2":
            bin_path = ask_path("Nhập đường dẫn file BIN gốc: ")
            if not bin_path.exists():
                print("[ERR] Không thấy file BIN.")
                return
            png_path = ask_path("Nhập đường dẫn file PNG: ")
            if not png_path.exists():
                print("[ERR] Không thấy file PNG.")
                return
            png_to_bin(bin_path, png_path)

        else:
            print("Chỉ nhập 1 hoặc 2.")
    except Exception as e:
        print("[FAIL]", e)

if __name__ == "__main__":
    main()
