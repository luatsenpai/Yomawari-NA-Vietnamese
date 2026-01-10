# -*- coding: utf-8 -*-
"""
nisfontbuild.py (tegaki-compatible + shared baseline)

FIX (2026-01-10):
- Đo bbox theo *baseline thật* bằng cách render thử (không phụ thuộc Pillow anchor/getbbox).
  -> tránh lỗi chữ có descender (q, p, g, j, y) bị lệch thấp so với chữ thường (u, a, ...).
  -> tránh lỗi glyph bị cắt (ví dụ chữ P mất 1 miếng) do tính bbox sai / thiếu bearing âm.
- Không "center" theo chiều dọc khi cell là hình vuông; phần dư dồn xuống dưới.
  -> baseline cao hơn, không bị cảm giác chữ bị "tụt" xuống.

Outputs:
- <ttf>.tga         (RGBA preview for PC)
- <ttf>.nltx        (Switch: NMPLTEX1 + YKCMP_V1, A8 swizzled block-linear like tegaki.nltx)
- <ttf>.nmf         (kept same format as your current script)
- <ttf>_atlas.png   (debug: grayscale atlas, to confirm glyphs were rendered)
"""

import struct
import zlib
import math
from pathlib import Path

from PIL import Image, ImageFont, ImageDraw


# ================== CHAR LIST ==================

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
    ascii_chars = ''.join(chr(c) for c in range(0x20, 0x7F))
    viet_sorted = ''.join(sorted(set(VIET_CHARS), key=ord))

    seen = set()
    out = []

    for ch in ascii_chars:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    for ch in viet_sorted:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    for ch in EXTRA_JP_CHARS:
        if ch not in seen:
            seen.add(ch)
            out.append(ch)

    return out


# ================== Baseline draw ==================

def draw_glyph_baseline_L(atlas: Image.Image,
                          draw: ImageDraw.ImageDraw,
                          x: int,
                          y_baseline: int,
                          ch: str,
                          font: ImageFont.FreeTypeFont,
                          fill: int = 255):
    """
    Draw with baseline anchor so all glyphs share the same baseline.
    Compatible with old Pillow (no anchor).
    """
    ascent, _ = font.getmetrics()
    try:
        draw.text((x, y_baseline), ch, font=font, fill=fill, anchor="ls")
    except TypeError:
        # Pillow too old -> position is top-left, so convert baseline->top
        draw.text((x, y_baseline - ascent), ch, font=font, fill=fill)


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


# ================== Robust bbox (baseline) ==================

def glyph_bbox_relative_to_baseline(font: ImageFont.FreeTypeFont, ch: str):
    """
    Trả về bbox (x0,y0,x1,y1) theo hệ tọa độ:
      - baseline nằm ở y=0
      - điểm đặt chữ (x_anchor) nằm ở x=0
    => y0 thường âm (phần trên baseline), y1 dương (phần dưới baseline)

    Cách làm: render thử lên ảnh đủ lớn rồi lấy getbbox().
    """
    ascent, _ = font.getmetrics()
    # canvas đủ lớn để không bị cắt khi glyph có bearing âm / accent cao
    fs = getattr(font, "size", 32) or 32
    W = H = max(256, fs * 8)
    pad = max(32, fs * 3)

    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    x_anchor = pad
    y_baseline = pad + ascent

    # draw
    try:
        d.text((x_anchor, y_baseline), ch, font=font, fill=255, anchor="ls")
    except TypeError:
        d.text((x_anchor, y_baseline - ascent), ch, font=font, fill=255)

    bbox = img.getbbox()
    if bbox is None:
        # space or invisible glyph
        return 0, 0, 0, 0

    x0, y0, x1, y1 = bbox
    # convert to baseline-relative
    return (x0 - x_anchor, y0 - y_baseline, x1 - x_anchor, y1 - y_baseline)


# ================== Measure tile with shared baseline ==================

def measure_font_and_tile(ttf_path: Path, font_size: int, chars):
    """
    Compute square tile that fits all glyphs AND provides one shared baseline.
    Robust across Pillow versions (no reliance on getbbox(anchor=...)).
    """
    font = ImageFont.truetype(str(ttf_path), font_size)

    # padding (tăng để tránh cắt nét như chữ P)
    pad_top = 6
    pad_bottom = 6
    pad_left = 6
    pad_right = 6

    max_left = 0
    max_right = 0
    max_up = 0
    max_down = 0

    for ch in chars:
        x0, y0, x1, y1 = glyph_bbox_relative_to_baseline(font, ch)
        max_left = max(max_left, -x0)
        max_right = max(max_right, x1)
        max_up = max(max_up, -y0)
        max_down = max(max_down, y1)

    base_w = max_left + max_right
    base_h = max_up + max_down

    total_w = base_w + pad_left + pad_right
    total_h = base_h + pad_top + pad_bottom

    # cell vuông, nhưng KHÔNG center theo chiều dọc (dồn phần dư xuống dưới)
    cell_side = max(total_w, total_h)

    # thêm 2px an toàn chống cắt biên do AA/hinting
    cell_side += 2

    extra_horz = cell_side - total_w
    left_pad = pad_left + (extra_horz // 2)

    # vertical: giữ top_pad cố định, phần dư xuống dưới => baseline cao hơn
    top_pad = pad_top

    baseline_y = top_pad + max_up
    x_origin = left_pad + max_left

    # cảnh báo nếu vượt 255 (vì NMF có field 1 byte)
    if cell_side > 255:
        print(f"[WARN] cell_side={cell_side} > 255. Nên giảm font size để tránh overflow trong NMF.")

    return {
        "font": font,
        "cell_w": cell_side,
        "cell_h": cell_side,
        "baseline_y": baseline_y,
        "x_origin": x_origin,
    }


# ================== Build atlas + NMF entries ==================

def align_up(value, align):
    return (value + align - 1) // align * align


def build_atlas_and_nmf(ttf_path: Path, font_size: int):
    chars = build_char_list()
    m = measure_font_and_tile(ttf_path, font_size, chars)

    font = m["font"]
    cell_w = m["cell_w"]
    cell_h = m["cell_h"]
    baseline_y = m["baseline_y"]
    x_origin = m["x_origin"]

    char_count = len(chars)

    texture_width = 2048
    cols = max(1, texture_width // cell_w)

    rows_used = (char_count + cols - 1) // cols
    height_raw = rows_used * cell_h
    height_tex = align_up(height_raw, 256)  # tegaki pads to multiple of 256

    atlas = Image.new("L", (texture_width, height_tex), 0)
    draw = ImageDraw.Draw(atlas)

    nmf_entries = []
    idx = 0
    for ch in chars:
        row = idx // cols
        col = idx % cols
        tile_x = col * cell_w
        tile_y = row * cell_h

        x_text = tile_x + x_origin
        y_base = tile_y + baseline_y
        draw_glyph_baseline_L(atlas, draw, x_text, y_base, ch, font, 255)

        ch_code = ord(ch)
        if ch_code > 0xFFFF:
            raise ValueError(f"Ký tự {repr(ch)} > 0xFFFF, không vừa u16.")

        nmf_entries.append((tile_x, tile_y, ch_code))
        idx += 1

    return atlas, nmf_entries, cell_w, cell_h, char_count, height_raw, height_tex


# ================== Build NMF (GIỮ NGUYÊN FORMAT CŨ của bạn) ==================

def build_nmf_binary(cell_w: int, cell_h: int, entries) -> bytes:
    magic = b"nismultifontfrm\x00"
    end = b"nis uti chu eoc "

    char_count = len(entries)
    size_field = char_count * 8 + 0x8

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

    # NOTE: 2 field dưới là 1 byte -> nếu cell_w/h >255 sẽ overflow
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


# ================== Build NLTX (Switch, tegaki-like) ==================

NMPLTEX_MAGIC = b"NMPLTEX1"
YKCMP_MAGIC = b"YKCMP_V1"

NLTEX_FIELD_10 = 0x00000064
NLTEX_FIELD_14 = 0x00800004
NLTEX_FIELD_20 = 0x00000001
NLTEX_FIELD_24 = 0x00020001
NLTEX_FIELD_28 = 0x00000200
NLTEX_FLAGS    = 0x00000205
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
    block_height = 32  # tegaki

    pixels = atlas.tobytes()
    dsize = width * height_tex
    if len(pixels) != dsize:
        raise ValueError("Số byte ảnh không khớp width * height_tex.")

    sw = Swizzler(width, bpp, block_height)
    swizzled = bytearray(dsize)
    for y in range(height_tex):
        row_off = y * width
        for x in range(width):
            dst_off = sw.get_offset(x, y)
            if dst_off < dsize:
                swizzled[dst_off] = pixels[row_off + x]

    zdata = zlib.compress(bytes(swizzled), 9)

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

    yk_chunk_size = 20 + len(zdata)

    yk = bytearray()
    yk += YKCMP_MAGIC
    yk += struct.pack("<III", 7, yk_chunk_size, dsize)
    yk += zdata

    out_path.write_bytes(header + yk)


# ================== Save TGA RGBA (PC preview) ==================

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
    print("=== Yomawari Font Builder (tegaki swizzle + shared baseline) ===")

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

    atlas, nmf_entries, cell_w, cell_h, char_count, height_raw, height_tex = \
        build_atlas_and_nmf(ttf_path, font_size)

    base = ttf_path.with_suffix("")
    tga_path = base.with_suffix(".tga")
    nltx_path = base.with_suffix(".nltx")
    nmf_path = base.with_suffix(".nmf")
    atlas_png = base.with_name(base.name + "_atlas.png")

    save_tga_rgba_from_l(atlas, tga_path)
    atlas.save(atlas_png)

    build_nltx_from_atlas(atlas, height_raw, height_tex, nltx_path)

    nmf_bytes = build_nmf_binary(cell_w, cell_h, nmf_entries)
    nmf_path.write_bytes(nmf_bytes)

    print("DONE")
    print(" -", tga_path)
    print(" -", atlas_png, "(debug)")
    print(" -", nltx_path)
    print(" -", nmf_path)
    print(f"Chars={char_count}  Cell={cell_w}x{cell_h}  Atlas=2048x{height_tex} (raw={height_raw})")


if __name__ == "__main__":
    main()
