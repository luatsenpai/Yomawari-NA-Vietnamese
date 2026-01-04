# -*- coding: utf-8 -*-
"""
Yomawari / NIS font builder (no template, square tiles, shared baseline):
- Input:  TTF + font size (dùng để render)
- Output:
  - .tga (RGBA) cho bản PC
  - .nltx (L8, NMPLTEX1 + YKCMP_V1 type 7) cho Switch
  - .nmf (chuẩn NISFONTBuilder / tegaki.nmf)

Charset tổng:
  1) ASCII printable 0x20–0x7E.
  2) Tiếng Việt (VIET_CHARS) – unique, sort theo ord().
  3) Bảng Shift-JIS console (EXTRA_JP_CHARS) – đúng thứ tự bạn đưa.

Mọi ký tự dùng chung một baseline (theo metrics ascent/descent của font).
"""

import struct
import zlib
import math
from pathlib import Path

from PIL import Image, ImageFont, ImageDraw


# ================== CHAR LIST ==================

# Bảng ký tự tiếng Việt
VIET_CHARS = (
    "áàảãạâấầẩẫậăắằẳẵặđ"
    "éèẻẽẹêếềểễệ"
    "íìỉĩị"
    "óòỏõọôốồổỗộơớờởỡợ"
    "úùủũụưứừửữự"
    "ýỳỷỹỵ"
    "ÁÀẢÃẠÂẤẦẨẪẬĂẮẰẲẴẶĐ"
    "ÉÈẺẼẸÊẾỀỂỄỆ"
    "ÍÌỈĨỊ"
    "ÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ"
    "ÚÙỦŨỤƯỨỪỬỮỰ"
    "ÝỲỶỸỴ"
)

# Bảng ký tự Shift-JIS mà console dùng (81 3F.., 82 3F..), biểu diễn bằng Unicode
EXTRA_JP_CHARS = (
    "　、。，．・：；？！゛゜´｀¨"
    "＾￣＿ヽヾゝゞ〃仝々〆〇ー―‐／"
    "＼～∥｜…‥‘’“”（）〔〕［］"
    "｛｝〈〉《》「」『』【】＋－±×"
    "÷＝≠＜＞≦≧∞∴♂♀°′″℃￥"
    "＄￠￡％＃＆＊＠§☆★○●◎◇"
    "◆□■△▲▽▼※〒→←↑↓〓"
    "∈∋⊆⊇⊂⊃"
    "∪∩∧∨￢⇒⇔∀"
    "∃∠⊥⌒∂"
    "∇≡≒≪≫√∽∝∵∫∬"
    "Å‰♯♭♪†‡¶◯"
    "０１２３４５６７８９"
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯ"
    "ＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏ"
    "ｐｑｒｓｔｕｖｗｘｙｚ"
)


def build_char_list():
    """
    Charset tổng:

    1) ASCII printable 0x20–0x7E (space, !, ", ..., ~) – đúng hex order.
    2) Tiếng Việt (VIET_CHARS) – unique, sort theo ord() (hex order).
    3) Bảng Shift-JIS console (EXTRA_JP_CHARS) – giữ đúng thứ tự như bảng bạn đưa.

    Nếu ký tự đã xuất hiện ở bước trước thì bỏ qua.
    """
    # 1) ASCII 0x20–0x7E
    ascii_chars = ''.join(chr(c) for c in range(0x20, 0x7F))

    # 2) Vietnamese sorted by codepoint
    viet_sorted = ''.join(sorted(set(VIET_CHARS), key=ord))

    seen = set()
    out = []

    # Add ASCII
    for ch in ascii_chars:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    # Add Vietnamese
    for ch in viet_sorted:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    # Add extra JP/console chars
    for ch in EXTRA_JP_CHARS:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    return out


# ================== Swizzler (Tegra block-linear) ==================

def count_lsb_zeros(value: int) -> int:
    c = 0
    while c < 32 and ((value >> c) & 1) == 0:
        c += 1
    return c


class Swizzler:
    """Swizzle kiểu Switch/Tegra (block-linear), bpp = bytes per pixel."""

    def __init__(self, width: int, bpp: int, block_height: int):
        self.bhMask = (block_height * 8) - 1
        self.bhShift = count_lsb_zeros(block_height * 8)
        self.bppShift = count_lsb_zeros(bpp)

        widthInGobs = math.ceil(width * bpp / 64.0)
        self.gobStride = 512 * block_height * widthInGobs
        self.xShift = count_lsb_zeros(block_height * 512)

    def get_offset(self, x: int, y: int) -> int:
        x <<= self.bppShift
        off = (y >> self.bhShift) * self.gobStride
        off += (x >> 6) << self.xShift
        off += ((y & self.bhMask) >> 3) << 9
        off += ((x & 0x3F) >> 5) << 8
        off += ((y & 0x07) >> 1) << 6
        off += ((x & 0x1F) >> 4) << 5
        off += ((y & 0x01) << 4)
        off += (x & 0x0F)
        return off


# ================== Đo font & layout tile vuông + baseline ==================

def measure_font_and_tile(ttf_path: Path, font_size: int, chars):
    """
    - Lấy ascent/descent của font.
    - Đo max advance width cho tập ký tự.
    - Tạo tile vuông đủ chứa ascent + descent + padding.
    - Tính baseline_y từ top tile.
    """
    font = ImageFont.truetype(str(ttf_path), font_size)
    ascent, descent = font.getmetrics()

    max_w = 0
    for ch in chars:
        mask = font.getmask(ch)
        w, h = mask.size
        if w > max_w:
            max_w = w

    # Padding cơ bản
    pad_top = 4
    pad_bottom = 4
    pad_left = 2
    pad_right = 2

    height_base = ascent + descent + pad_top + pad_bottom
    width_base = max_w + pad_left + pad_right

    # Ô vuông
    cell_side = max(height_base, width_base)
    extra_vert = cell_side - height_base
    extra_horz = cell_side - width_base

    top_pad = pad_top + extra_vert // 2
    bottom_pad = pad_bottom + (extra_vert - extra_vert // 2)
    left_pad = pad_left + extra_horz // 2
    right_pad = pad_right + (extra_horz - extra_horz // 2)

    cell_w = cell_side
    cell_h = cell_side

    baseline_y = top_pad + ascent  # từ top tile

    metrics = {
        "font": font,
        "cell_w": cell_w,
        "cell_h": cell_h,
        "baseline_y": baseline_y,
        "left_pad": left_pad,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "ascent": ascent,
        "descent": descent,
    }
    return metrics


# ================== Build atlas + NMF info ==================

def align_up(value, align):
    return (value + align - 1) // align * align


def build_atlas_and_nmf(ttf_path: Path, font_size: int):
    chars = build_char_list()
    m = measure_font_and_tile(ttf_path, font_size, chars)
    font = m["font"]
    cell_w = m["cell_w"]
    cell_h = m["cell_h"]
    baseline_y = m["baseline_y"]
    left_pad = m["left_pad"]
    ascent = m["ascent"]

    char_count = len(chars)
    texture_width = 2048
    cols = max(1, texture_width // cell_w)

    rows_used = (char_count + cols - 1) // cols
    height_raw = rows_used * cell_h
    height_tex = align_up(height_raw, 256)

    atlas = Image.new("L", (texture_width, height_tex), 0)
    draw = ImageDraw.Draw(atlas)

    nmf_entries = []
    idx = 0
    for ch in chars:
        row = idx // cols
        col = idx % cols
        tile_x = col * cell_w
        tile_y = row * cell_h

        # vẽ bằng baseline chung
        x_text = tile_x + left_pad
        y_text = tile_y + (baseline_y - ascent)
        draw.text((x_text, y_text), ch, font=font, fill=255)

        ch_code = ord(ch)
        if ch_code > 0xFFFF:
            raise ValueError(f"Ký tự {repr(ch)} > 0xFFFF, không vừa u16.")

        nmf_entries.append((tile_x, tile_y, ch_code))
        idx += 1

    return atlas, nmf_entries, cell_w, cell_h, char_count, height_raw, height_tex


# ================== Build NMF (y hệt tegaki.nmf) ==================

def build_nmf_binary(cell_w: int, cell_h: int, entries) -> bytes:
    magic = b"nismultifontfrm\x00"
    end = b"nis uti chu eoc "

    char_count = len(entries)
    size_field = char_count * 8 + 0x8

    # copy từ tegaki.nmf
    unk0 = 0x00903A00
    unk1 = 0x0000000F
    unk2 = 0x0080FC88
    unk3 = 0x90385D00

    buf = bytearray()
    buf += magic
    buf += struct.pack("<I", 0)
    buf += struct.pack("<I", size_field)
    buf += struct.pack("<I", size_field)
    buf += struct.pack("<I", 0)

    buf += bytes([
        0,
        cell_w & 0xFF,
        cell_h & 0xFF,
        0
    ])

    buf += struct.pack("<H", cell_h & 0xFFFF)      # fontSize = fontHeight
    buf += struct.pack("<H", char_count & 0xFFFF)

    for x, y, code in entries:
        buf += struct.pack("<HHHH",
                           x & 0xFFFF,
                           y & 0xFFFF,
                           code & 0xFFFF,
                           0)

    buf += end
    buf += struct.pack("<IIII", unk0, unk1, unk2, unk3)

    return bytes(buf)


# ================== Build NLTX (Switch) ==================

NMPLTEX_MAGIC = b"NMPLTEX1"
YKCMP_MAGIC = b"YKCMP_V1"

NLTEX_FIELD_10 = 0x00000064      # 100
NLTEX_FIELD_14 = 0x00800004
NLTEX_FIELD_20 = 0x00000001
NLTEX_FIELD_24 = 0x00020001
NLTEX_FIELD_28 = 0x00000200      # 512
NLTEX_FLAGS    = 0x00000205      # blockHeight=2^5=32
NLTEX_FIELD_34 = 0x00010007


def build_nltx_from_atlas(atlas: Image.Image,
                          height_raw: int,
                          height_tex: int,
                          out_path: Path):
    atlas = atlas.convert("L")
    width, h = atlas.size
    if h != height_tex:
        raise ValueError("Chiều cao atlas không khớp height_tex.")

    bpp = 1
    block_height = 32

    pixels = atlas.tobytes()
    if len(pixels) != width * height_tex:
        raise ValueError("Số byte ảnh không khớp width * height_tex.")

    linear = pixels
    dsize = width * height_tex

    sw = Swizzler(width, bpp, block_height)
    swizzled = bytearray(dsize)
    for y in range(height_tex):
        for x in range(width):
            src_off = y * width + x
            dst_off = sw.get_offset(x, y)
            if dst_off < len(swizzled):
                swizzled[dst_off] = linear[src_off]

    zdata = zlib.compress(bytes(swizzled))

    header = bytearray(0x80)
    header[0:8] = NMPLTEX_MAGIC

    struct.pack_into("<IIIIIIII", header, 0x10,
                     NLTEX_FIELD_10,
                     NLTEX_FIELD_14,
                     width,
                     height_raw,
                     NLTEX_FIELD_20,
                     NLTEX_FIELD_24,
                     NLTEX_FIELD_28,
                     dsize)

    struct.pack_into("<IIII", header, 0x30,
                     NLTEX_FLAGS,
                     NLTEX_FIELD_34,
                     0,
                     0)

    yk = bytearray()
    yk += YKCMP_MAGIC
    yk += struct.pack("<III", 7, len(zdata), dsize)
    yk += zdata

    out_path.write_bytes(header + yk)


# ================== Save TGA RGBA (PC) ==================

def save_tga_rgba_from_l(atlas: Image.Image, out_path: Path):
    l = atlas.convert("L")
    w, h = l.size
    gray = list(l.getdata())
    rgba_data = [(v, v, v, v) for v in gray]
    img = Image.new("RGBA", (w, h))
    img.putdata(rgba_data)
    img.save(out_path, format="TGA")


# ================== CLI ==================

def main():
    print("=== Yomawari Font Builder (baseline + JP console charset) ===")

    ttf_str = input("Đường dẫn file .ttf: ").strip().strip('"')
    size_str = input("Font size (px): ").strip()

    ttf_path = Path(ttf_str)
    if not ttf_path.exists():
        print("Không tìm thấy file TTF:", ttf_path)
        return

    try:
        font_size = int(size_str)
    except ValueError:
        print("Font size phải là số nguyên.")
        return

    print("Đang build atlas + NMF từ TTF...")

    atlas, nmf_entries, cell_w, cell_h, char_count, height_raw, height_tex = \
        build_atlas_and_nmf(ttf_path, font_size)

    base = ttf_path.with_suffix("")
    tga_path = base.with_suffix(".tga")
    nltx_path = base.with_suffix(".nltx")
    nmf_path = base.with_suffix(".nmf")

    save_tga_rgba_from_l(atlas, tga_path)
    print(f"- Đã lưu TGA RGBA: {tga_path}")

    build_nltx_from_atlas(atlas, height_raw, height_tex, nltx_path)
    print(f"- Đã lưu NLTX: {nltx_path}")

    nmf_bytes = build_nmf_binary(cell_w, cell_h, nmf_entries)
    nmf_path.write_bytes(nmf_bytes)
    print(f"- Đã lưu NMF: {nmf_path}")

    print()
    print(f"> Số ký tự    : {char_count}")
    print(f"> Ô glyph     : {cell_w} x {cell_h} (ô vuông)")
    print(f"> Atlas size  : {atlas.width} x {atlas.height} (height_raw = {height_raw})")


if __name__ == "__main__":
    main()
