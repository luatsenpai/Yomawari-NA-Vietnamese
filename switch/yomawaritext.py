import sys
from pathlib import Path

def export(dat_path):
    dat = Path(dat_path)
    with open(dat, 'rb') as f:
        files_count = int.from_bytes(f.read(4), 'little')
        f.seek(0x0C)
        texts = []
        for _ in range(files_count):
            pos = f.tell()
            offset = int.from_bytes(f.read(4), 'little') + 4
            f.seek(offset)
            str_bytes = b''
            while True:
                b = f.read(1)
                if not b or b == b'\x00':
                    break
                str_bytes += b
            text = str_bytes.decode('utf-8', errors='replace')
            text = text.replace('\r\n', '<cf>').replace('\n', '<lf>').replace('\r', '<cr>')
            texts.append(text)
            f.seek(pos + 0x0C)
    txt_out = dat.with_suffix('.txt')
    with open(txt_out, 'w', encoding='utf-8') as out:
        out.write('\n'.join(texts))
    print(f"Xuất xong: {txt_out}")

def import_text(dat_path, txt_path):
    txt = Path(txt_path)
    dat = Path(dat_path)
    with open(txt, 'r', encoding='utf-8') as t:
        lines = t.read().splitlines()
    if len(lines) == 0:
        print("File TXT rỗng")
        return
    with open(dat, 'rb') as f:
        files_count = int.from_bytes(f.read(4), 'little')
        if files_count != len(lines):
            print("Số dòng TXT không khớp với số entries trong DAT")
            return
        f.seek(0x0C)
        base = int.from_bytes(f.read(4), 'little') + 4
        f.seek(0)
        header = bytearray(f.read(base))
        offset = base
        pos = 0x0C
        newtext = b'\x00'
        for i in range(files_count):
            line = lines[i]
            line = line.replace('<cf>', '\r\n').replace('<lf>', '\n').replace('<cr>', '\r')
            bnew = line.encode('utf-8')
            if i == files_count - 1:
                bnew += b'\x00'
            else:
                bnew += b'\x00\x00'
            newlen = len(bnew)
            newtext += bnew
            value = offset - 4
            header[pos:pos+4] = value.to_bytes(4, 'little')
            offset += newlen
            pos += 0x0C
    newfile = header + newtext[1:]
    new_dat = dat.parent / (dat.stem + '_new' + dat.suffix)
    with open(new_dat, 'wb') as out:
        out.write(newfile)
    print(f"Nhập xong: {new_dat}")

print("Tool gộp export/import cho Yomawari")
print("1. Xuất text từ DAT sang TXT (hỏi đường dẫn DAT, xuất TXT cùng tên)")
print("2. Nhập text từ TXT vào DAT (hỏi đường dẫn DAT và TXT, xuất DAT cùng tên thêm _new)")
choice = input("Chọn (1/2): ").strip()
if choice == '1':
    dat_path = input("Đường dẫn file: ").strip()
    export(dat_path)
elif choice == '2':
    dat_path = input("Đường dẫn file DAT: ").strip()
    txt_path = input("Đường dẫn file TXT: ").strip()
    import_text(dat_path, txt_path)
else:
    print("Lựa chọn không hợp lệ")
    sys.exit(1)