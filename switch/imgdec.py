# texbin_extract.py
import struct
from pathlib import Path
from PIL import Image

# ---------- Morton helpers ----------
def part1by1_16(n: int) -> int:
    n &= 0xFFFF
    n = (n | (n << 8)) & 0x00FF00FF
    n = (n | (n << 4)) & 0x0F0F0F0F
    n = (n | (n << 2)) & 0x33333333
    n = (n | (n << 1)) & 0x55555555
    return n

def morton2(x: int, y: int) -> int:
    return part1by1_16(x) | (part1by1_16(y) << 1)

def is_pow2(x: int) -> bool:
    return x > 0 and (x & (x - 1)) == 0

def unswizzle_morton_pixels(raw: bytes, w: int, h: int, bpp: int) -> bytes:
    mx = [part1by1_16(x) for x in range(w)]
    my = [part1by1_16(y) for y in range(h)]
    out = bytearray(len(raw))
    mv = memoryview(raw)
    ov = memoryview(out)
    for y in range(h):
        row_off = y * w * bpp
        base = my[y] << 1
        for x in range(w):
            src = (base | mx[x]) * bpp
            dst = row_off + x * bpp
            ov[dst:dst + bpp] = mv[src:src + bpp]
    return bytes(out)

def unswizzle_morton_blocks(raw: bytes, w: int, h: int, bpp: int, bw: int, bh: int) -> bytes:
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

# ---------- Channel reorder ----------
def reorder_rgba(raw_rgba: bytes, order: str) -> bytes:
    order = order.upper()
    if order == "RGBA":
        return raw_rgba
    idx = {"R": 0, "G": 1, "B": 2, "A": 3}
    o = [idx[c] for c in order]
    src = memoryview(raw_rgba)
    out = bytearray(len(raw_rgba))
    for i in range(0, len(raw_rgba), 4):
        out[i + 0] = src[i + o[0]]
        out[i + 1] = src[i + o[1]]
        out[i + 2] = src[i + o[2]]
        out[i + 3] = src[i + o[3]]
    return bytes(out)

# ---------- Transparency (RGB -> Alpha) ----------
def make_transparent(img: Image.Image, alpha_from="AUTO", rescale=True, p_low=5.0, p_high=99.0) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    mv = memoryview(img.tobytes())

    # detect alpha useful?
    a_min, a_max = 255, 0
    step = max(1, (w * h) // 200000)
    for i in range(0, w * h, step):
        a = mv[i * 4 + 3]
        a_min = min(a_min, a)
        a_max = max(a_max, a)
        if a_min == 0 and a_max == 255:
            break

    alpha_from = alpha_from.upper()
    if alpha_from == "AUTO":
        use = "A" if not (a_min == 255 and a_max == 255) else "MAX"
    else:
        use = alpha_from

    alpha = bytearray(w * h)
    for i in range(w * h):
        r = mv[i * 4 + 0]
        g = mv[i * 4 + 1]
        b = mv[i * 4 + 2]
        a = mv[i * 4 + 3]
        if use == "A": alpha[i] = a
        elif use == "R": alpha[i] = r
        elif use == "G": alpha[i] = g
        elif use == "B": alpha[i] = b
        elif use == "MAX": alpha[i] = max(r, g, b)
        else: raise ValueError("alpha_from chỉ nhận AUTO/A/R/G/B/MAX")

    if rescale:
        vals = sorted(alpha)
        lo = vals[int(len(vals) * (p_low / 100.0))]
        hi = vals[int(len(vals) * (p_high / 100.0))]
        if hi <= lo:
            lo = min(alpha)
            hi = max(alpha) if max(alpha) > lo else lo + 1
        scale = 255.0 / (hi - lo)
        for i, v in enumerate(alpha):
            x = int((v - lo) * scale)
            if x < 0: x = 0
            if x > 255: x = 255
            alpha[i] = x

    out = bytearray(w * h * 4)
    ov = memoryview(out)
    for i in range(w * h):
        j = i * 4
        ov[j + 0] = 255
        ov[j + 1] = 255
        ov[j + 2] = 255
        ov[j + 3] = alpha[i]
    return Image.frombytes("RGBA", (w, h), bytes(out))

# ---------- Decode ----------
def decode_p8_with_palette(pal_rgba: bytes, idx: bytes, w: int, h: int, pal_order="RGBA") -> Image.Image:
    pal_order = pal_order.upper()
    pidx = {"R":0,"G":1,"B":2,"A":3}
    o = [pidx[c] for c in pal_order]
    mv = memoryview(pal_rgba)

    pal = []
    for i in range(0, 0x400, 4):
        pal.append((mv[i+o[0]], mv[i+o[1]], mv[i+o[2]], mv[i+o[3]]))

    out = bytearray(w*h*4)
    ov = memoryview(out)
    for i, pi in enumerate(idx):
        r,g,b,a = pal[pi]
        j=i*4
        ov[j:j+4] = bytes((r,g,b,a))
    return Image.frombytes("RGBA",(w,h),bytes(out))

def decode_16bpp(raw: bytes, w: int, h: int, kind="rgb565") -> Image.Image:
    mv = memoryview(raw)
    out = bytearray(w*h*4)
    ov = memoryview(out)
    for i in range(w*h):
        v = mv[i*2] | (mv[i*2+1] << 8)
        if kind == "rgb565":
            r = (v >> 11) & 0x1F
            g = (v >> 5) & 0x3F
            b = v & 0x1F
            r = (r*255)//31; g=(g*255)//63; b=(b*255)//31
            a = 255
        elif kind == "argb4444":
            a = ((v>>12)&0xF)*17
            r = ((v>>8)&0xF)*17
            g = ((v>>4)&0xF)*17
            b = (v&0xF)*17
        elif kind == "argb1555":
            a = 255 if ((v>>15)&1) else 0
            r = (v>>10)&0x1F; g=(v>>5)&0x1F; b=v&0x1F
            r=(r*255)//31; g=(g*255)//31; b=(b*255)//31
        else:
            raise ValueError("fmt16 chỉ: rgb565/argb4444/argb1555")
        j=i*4
        ov[j:j+4] = bytes((r,g,b,a))
    return Image.frombytes("RGBA",(w,h),bytes(out))

# ---------- Parse header (FIX) ----------
def parse_tex(buf: bytes) -> dict:
    u32 = lambda off: struct.unpack_from("<I", buf, off)[0]
    u16 = lambda off: struct.unpack_from("<H", buf, off)[0]

    file_size = u32(0x08)
    data_size = u32(0x10)  # luôn = file_size - 0x30 với tất cả file bạn gửi
    w = u16(0x18); h = u16(0x1A)
    fmt = u16(0x1E)
    flags = u32(0x28)

    if len(buf) != file_size:
        # vẫn tiếp tục, nhưng báo để biết file có padding / stream khác
        pass

    data_off = 0x30
    data = buf[data_off:data_off+data_size]
    if len(data) < data_size:
        raise ValueError("data_size vượt quá file")

    return dict(w=w,h=h,fmt=fmt,flags=flags,file_size=file_size,data_size=data_size,data=data)

# ---------- Swizzle policy ----------
def unswizzle_variants(pixel: bytes, w: int, h: int, bpp: int, mode: str, flags: int) -> dict:
    mode = mode.lower()
    if mode == "auto":
        # Vita: flags==0x304 hay swizzle -> dùng block 8x8
        if flags == 0x304 and is_pow2(w) and is_pow2(h):
            return {"b8": unswizzle_morton_blocks(pixel,w,h,bpp,8,8)}
        return {"lin": pixel}
    if mode == "none":
        return {"lin": pixel}
    if mode == "px":
        return {"px": unswizzle_morton_pixels(pixel,w,h,bpp)}
    if mode == "b8":
        return {"b8": unswizzle_morton_blocks(pixel,w,h,bpp,8,8)}
    if mode == "tryall":
        return {
            "lin": pixel,
            "px": unswizzle_morton_pixels(pixel,w,h,bpp),
            "b8": unswizzle_morton_blocks(pixel,w,h,bpp,8,8),
            "b16x8": unswizzle_morton_blocks(pixel,w,h,bpp,16,8),
            "b32x8": unswizzle_morton_blocks(pixel,w,h,bpp,32,8),
        }
    raise ValueError("swz chỉ: auto/none/px/b8/tryall")

# ---------- Main extract ----------
def extract_file(path: Path, swz: str, order: str, pal_order: str, fmt16: str,
                 transparent: bool, alpha_from: str):
    buf = path.read_bytes()
    info = parse_tex(buf)
    w,h,fmt,flags = info["w"],info["h"],info["fmt"],info["flags"]
    data = info["data"]
    data_size = info["data_size"]

    def save(img: Image.Image, outp: Path):
        if transparent:
            img = make_transparent(img, alpha_from=alpha_from)
        img.save(outp)

    # Case P8 + palette (file_00, file_04)
    # data layout: [palette 0x400][indices w*h]
    if data_size >= 0x400 and (data_size - 0x400) == (w*h) and fmt == 8:
        pal = data[:0x400]
        idx = data[0x400:]
        # swizzle áp cho index (nếu có)
        variants = unswizzle_variants(idx, w, h, 1, swz, flags)
        for suf, idx2 in variants.items():
            img = decode_p8_with_palette(pal, idx2, w, h, pal_order=pal_order)
            outp = path.with_name(path.stem + f"_{suf}.png")
            save(img, outp)
        print(f"[OK] {path.name} P8+PAL {w}x{h} flags=0x{flags:X} -> {len(variants)} file(s)")
        return

    # Case RGBA8888 (file_02/08/09): data layout [pixels w*h*4]
    if data_size >= w*h*4:
        # lấy base level đầu tiên
        px = data[:w*h*4]
        variants = unswizzle_variants(px, w, h, 4, swz, flags)
        for suf, px2 in variants.items():
            px2 = reorder_rgba(px2, order)
            img = Image.frombytes("RGBA",(w,h),px2)
            outp = path.with_name(path.stem + f"_{suf}.png")
            save(img, outp)
        print(f"[OK] {path.name} 32bpp {w}x{h} fmt=0x{fmt:X} flags=0x{flags:X} -> {len(variants)} file(s)")
        return

    # Case 16bpp
    if data_size >= w*h*2:
        px = data[:w*h*2]
        variants = unswizzle_variants(px, w, h, 2, swz, flags)
        for suf, px2 in variants.items():
            img = decode_16bpp(px2, w, h, kind=fmt16)
            outp = path.with_name(path.stem + f"_{suf}.png")
            save(img, outp)
        print(f"[OK] {path.name} 16bpp({fmt16}) {w}x{h} -> {len(variants)} file(s)")
        return

    # L8 fallback
    if data_size >= w*h:
        img = Image.frombytes("L",(w,h), data[:w*h]).convert("RGBA")
        outp = path.with_suffix(".png")
        save(img, outp)
        print(f"[OK] {path.name} L8 {w}x{h}")
        return

    raise ValueError(f"Không nhận dạng được layout: data_size=0x{data_size:X}, w*h=0x{w*h:X}, fmt=0x{fmt:X}")

def iter_inputs(p: Path):
    if p.is_file():
        yield p
    else:
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".bin",".dat",".tex",".img"):
                yield f

def main():
    import argparse
    ap = argparse.ArgumentParser("Extract TEXBIN -> PNG (FIX offset, Vita swizzle, transparent)")
    ap.add_argument("input", help="file hoặc folder")
    ap.add_argument("--swz", default="auto", choices=["auto","none","px","b8","tryall"])
    ap.add_argument("--order", default="RGBA", help="RGBA/BGRA/ARGB/ABGR")
    ap.add_argument("--pal-order", default="RGBA", help="RGBA/BGRA/ARGB/ABGR")
    ap.add_argument("--fmt16", default="rgb565", choices=["rgb565","argb4444","argb1555"])

    ap.add_argument("--transparent", action="store_true", help="xuất PNG trong suốt (RGB->Alpha)")
    ap.add_argument("--alpha-from", default="AUTO", choices=["AUTO","A","R","G","B","MAX"])

    args = ap.parse_args()
    target = Path(args.input)
    if not target.exists():
        raise SystemExit("Input không tồn tại")

    for f in iter_inputs(target):
        try:
            extract_file(
                f,
                swz=args.swz,
                order=args.order,
                pal_order=args.pal_order,
                fmt16=args.fmt16,
                transparent=args.transparent,
                alpha_from=args.alpha_from,
            )
        except Exception as e:
            print(f"[FAIL] {f.name}: {e}")

if __name__ == "__main__":
    main()
