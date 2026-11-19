import os, sys, json, struct, subprocess, shutil
from pathlib import Path

SECTOR = 0x800
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR   = os.path.join(SCRIPT_DIR, "tools")
FFMPEG      = "ffmpeg"
FFPROBE     = "ffprobe"
ADX_ENCODER = os.path.join(TOOLS_DIR, "radx_encode.exe")

def run(cmd, desc, cwd=None):
    print(f"[*] {desc}")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("[!] ОШИБКА:\n" + (r.stderr or r.stdout))
        sys.exit(1)
    print("[+] OK")

def probe(path):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-show_format", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(r.stdout)

def pack_len(buf, pos):
    b = buf[pos + 4]
    if (b & 0xF0) == 0x20: return 12                       # MPEG-1 pack
    if (b & 0xC0) == 0x40: return 14 + (buf[pos + 13] & 7) # MPEG-2 pack
    raise ValueError(f"неизвестный pack header на {pos:#x}")

def pes_payload(buf, off):
    sid  = buf[off + 3]
    plen = int.from_bytes(buf[off + 4:off + 6], "big")
    p, end = off + 6, off + 6 + plen
    if sid in (0xBB, 0xBC, 0xBE, 0xBF):
        return sid, p, end
    if (buf[p] & 0xC0) == 0x80:            # MPEG-2 PES
        p += 3 + buf[p + 2]
    else:                                  # MPEG-1 PES
        n = 0
        while p < end and buf[p] == 0xFF and n < 16: p += 1; n += 1
        if p < end and (buf[p] & 0xC0) == 0x40: p += 2
        if p < end:
            m = buf[p] & 0xF0
            if   m == 0x20: p += 5
            elif m == 0x30: p += 10
            elif buf[p] == 0x0F: p += 1
    return sid, p, end

def scan_audio(buf, aid):
    chunks, pos = [], 0
    while pos + SECTOR <= len(buf):
        if buf[pos:pos + 4] == b"\x00\x00\x01\xb9": break
        if buf[pos:pos + 4] != b"\x00\x00\x01\xba":
            pos += SECTOR; continue
        off, stop = pos + pack_len(buf, pos), pos + SECTOR
        while off + 6 <= stop and buf[off:off + 3] == b"\x00\x00\x01":
            sid, s, e = pes_payload(buf, off)
            if sid == aid: chunks.append((s, e))
            off = e
        pos += SECTOR
    return chunks

def main():
    if len(sys.argv) < 4:
        print("Использование: python script.py <orig.sfd> <new_audio.wav> <out.sfd>")
        sys.exit(1)

    orig_sfd, new_audio, out_sfd = (os.path.abspath(x) for x in sys.argv[1:4])
    work = os.path.join(SCRIPT_DIR, "temp_work")
    os.makedirs(work, exist_ok=True)

    print("=" * 70)
    print(" ПОДМЕНА АУДИО В SFD БЕЗ ПЕРЕСБОРКИ КОНТЕЙНЕРА")
    print("=" * 70)

    data = bytearray(Path(orig_sfd).read_bytes())

    info = probe(orig_sfd)
    a  = next(s for s in info["streams"] if s["codec_type"] == "audio")
    sr, ch = int(a["sample_rate"]), int(a["channels"])
    print(f"\n[1] Оригинал: {sr} Гц, {ch} кан.")

    aids = sorted({pes_payload(data, pos + pack_len(data, pos))[0]
                   for pos in range(0, len(data) - SECTOR + 1, SECTOR)
                   if data[pos:pos + 4] == b"\x00\x00\x01\xba"
                   and data[pos + pack_len(data, pos):pos + pack_len(data, pos) + 3] == b"\x00\x00\x01"
                   and 0xC0 <= data[pos + pack_len(data, pos) + 3] <= 0xDF})
    if not aids:
        print("[!] Аудиопоток не найден."); sys.exit(1)
    aid = aids[0]
    if len(aids) > 1:
        print(f"    ВНИМАНИЕ: найдено несколько аудиодорожек {[hex(x) for x in aids]}, меняю {hex(aid)}")

    chunks = scan_audio(data, aid)
    orig_sfa = b"".join(data[s:e] for s, e in chunks)
    print(f"[2] Аудио {hex(aid)}: {len(chunks)} пакетов, {len(orig_sfa)} байт")

    hdr_size = int.from_bytes(orig_sfa[2:4], "big") + 4
    header   = orig_sfa[:hdr_size]
    if header[0x11A:0x120] != b"(c)CRI":
        print(f"    (проверка CRI-метки: {header[0x11A:0x120]!r} — оставляем как в оригинале)")
    target = len(orig_sfa) - hdr_size
    print(f"    Заголовок {hdr_size} Б, данных {target} Б")

    bytes_per_sec = sr / 32 * 18 * ch
    need_sec = target / bytes_per_sec + 0.25

    print(f"\n[3] Подготовка звука ({need_sec:.2f} с с запасом тишины)")
    wav = os.path.join(work, "new.wav")
    run([FFMPEG, "-y", "-i", new_audio, "-ar", str(sr), "-ac", str(ch),
         "-af", "apad", "-t", f"{need_sec:.3f}", "-c:a", "pcm_s16le", wav],
        "ресемплинг + добивка тишиной", cwd=work)

    print("\n[4] Кодирование в ADX")
    adx_path = os.path.join(work, "new.adx")
    run([ADX_ENCODER, wav, adx_path], "radx_encode", cwd=work)
    adx = Path(adx_path).read_bytes()

    if adx[4:8] != header[4:8]:
        print(f"    параметры кодирования отличаются: {adx[4:8].hex()} vs {header[4:8].hex()} — беру из нового ADX")
        header = header[:4] + adx[4:8] + header[8:]
    if int.from_bytes(adx[8:12], "big") != sr:
        print("[!] Частота нового ADX не совпадает."); sys.exit(1)

    new_data = adx[int.from_bytes(adx[2:4], "big") + 4:]
    if len(new_data) < target:
        new_data += b"\x00" * (target - len(new_data))
    new_sfa = header + new_data[:target]
    assert len(new_sfa) == len(orig_sfa)
    print(f"[5] Новый SFA собран: {len(new_sfa)} байт (ровно как оригинал)")

    out = bytearray(data)
    p = 0
    for s, e in chunks:
        L = e - s
        out[s:e] = new_sfa[p:p + L]
        p += L
    assert p == len(new_sfa)

    changed = sum(1 for i in range(len(data)) if data[i] != out[i])
    print(f"[6] Записано {p} байт в {len(chunks)} пакетов; изменено байт всего: {changed}")

    os.makedirs(os.path.dirname(out_sfd), exist_ok=True)
    Path(out_sfd).write_bytes(out)

    chk = probe(out_sfd)
    ca = next((s for s in chk["streams"] if s["codec_type"] == "audio"), None)
    print("\n" + "=" * 70)
    print(f"[+] ГОТОВО: {out_sfd}")
    print(f"    Размер: {len(data)} -> {len(out)} байт (разница {len(out)-len(data)})")
    print(f"    Аудио в результате: "
          f"{ca['codec_name']}, {ca['sample_rate']} Гц, {ca['channels']} кан." if ca else "    АУДИО НЕ НАЙДЕНО!")
    print(f"    Длительность: {float(chk['format']['duration']):.2f} с "
          f"(оригинал {float(info['format']['duration']):.2f} с)")
    print("=" * 70)

    shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    main()