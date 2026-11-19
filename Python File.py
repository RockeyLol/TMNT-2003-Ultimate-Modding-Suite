import os, sys, json, struct, subprocess, shutil, time
from pathlib import Path

# ==================== НАСТРОЙКИ ====================
VIDEO_ROOT = Path(r"E:\Проекты\Черепашки\PS2\eng")
AUDIO_ROOT = Path(r"E:\Проекты\Черепашки\озвучка\TMNT VIDEO")
OUT_ROOT   = Path(r"E:\Проекты\Черепашки\PS2\unpackru")

PAIRS = [                      # (папка с видео, папка с озвучкой)
    ("TMNT_PV0", "TMNT_V00"),
    ("TMNT_PV1", "TMNT_V01"),
    ("TMNT_PV2", "TMNT_V02"),
]

AUDIO_EXTS   = (".wav", ".flac", ".mp3", ".ogg", ".mka")
SKIP_EXISTING = True           # не переделывать уже готовые файлы (--force отключает)
DUR_TOLERANCE = 0.6            # допустимое расхождение длительности, сек

SCRIPT_DIR  = Path(__file__).resolve().parent
TOOLS_DIR   = SCRIPT_DIR / "tools"
FFMPEG      = "ffmpeg"
FFPROBE     = "ffprobe"
ADX_ENCODER = TOOLS_DIR / "radx_encode.exe"
TMP_ROOT    = SCRIPT_DIR / "_tmp"

SECTOR    = 0x800
PACK_START = b"\x00\x00\x01\xba"
PROG_END   = b"\x00\x00\x01\xb9"

DRY_RUN = "--dry-run" in sys.argv
FORCE   = "--force"   in sys.argv

# ==================== ПАРСЕР SFD ====================
def pack_len(buf, pos):
    b = buf[pos + 4]
    if (b & 0xF0) == 0x20: return 12                        # MPEG-1 pack header
    if (b & 0xC0) == 0x40: return 14 + (buf[pos + 13] & 7)  # MPEG-2 pack header
    raise ValueError(f"неизвестный pack header @{pos:#x}")

def pes_payload(buf, off):
    sid  = buf[off + 3]
    plen = int.from_bytes(buf[off + 4:off + 6], "big")
    p, end = off + 6, off + 6 + plen
    if sid in (0xBB, 0xBC, 0xBE, 0xBF):
        return sid, p, end
    if (buf[p] & 0xC0) == 0x80:                 # MPEG-2 PES
        p += 3 + buf[p + 2]
    else:                                       # MPEG-1 PES
        n = 0
        while p < end and buf[p] == 0xFF and n < 16: p += 1; n += 1
        if p < end and (buf[p] & 0xC0) == 0x40: p += 2
        if p < end:
            m = buf[p] & 0xF0
            if   m == 0x20: p += 5
            elif m == 0x30: p += 10
            elif buf[p] == 0x0F: p += 1
    return sid, p, end

def scan_audio(data):
    """{stream_id: [(payload_start, payload_end), ...]} для аудио 0xC0..0xDF"""
    streams, pos, n = {}, 0, len(data)
    while pos + SECTOR <= n:
        if data[pos:pos + 4] == PROG_END: break
        if data[pos:pos + 4] != PACK_START:
            pos += SECTOR; continue
        try:
            ph = pack_len(data, pos)
        except ValueError:
            pos += SECTOR; continue
        off, stop = pos + ph, pos + SECTOR
        while off + 6 <= stop and data[off:off + 3] == b"\x00\x00\x01":
            sid, s, e = pes_payload(data, off)
            if 0xC0 <= sid <= 0xDF:
                streams.setdefault(sid, []).append((s, e))
            if e <= off: break
            off = e
        pos += SECTOR
    return streams

# ==================== ВСПОМОГАТЕЛЬНОЕ ====================
def run(cmd, cwd=None):
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       cwd=str(cwd) if cwd else None,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[-1200:])
    return r.stdout

def probe(path):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffprobe не смог прочитать файл")
    return json.loads(r.stdout)

def audio_index(adir):
    idx = {}
    if not adir.is_dir(): return idx
    for p in adir.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            idx.setdefault(p.stem.lower(), []).append(p)
    return idx

def pick_audio(idx, adir, rel_sub, stem):
    cands = idx.get(stem.lower(), [])
    if not cands: return None
    same = [p for p in cands if p.parent == adir / rel_sub]
    root = [p for p in cands if p.parent == adir]
    return (same or root or cands)[0]

# ==================== ОБРАБОТКА ОДНОГО ФАЙЛА ====================
def process(sfd_path, wav_path, out_path):
    data = bytearray(sfd_path.read_bytes())

    info = probe(sfd_path)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if a is None:
        raise RuntimeError("в оригинале нет аудиодорожки")
    sr, ch = int(a["sample_rate"]), int(a["channels"])
    dur_o = float(info["format"].get("duration", 0) or 0)

    streams = scan_audio(data)
    if not streams:
        raise RuntimeError("аудиопакеты 0xC0..0xDF не найдены")
    aid = sorted(streams)[0]
    chunks = streams[aid]
    if len(streams) > 1:
        print(f"      несколько дорожек {[hex(x) for x in sorted(streams)]}, меняю {hex(aid)}")

    orig_sfa = b"".join(data[s:e] for s, e in chunks)
    if orig_sfa[:2] != b"\x80\x00":
        raise RuntimeError(f"аудио не ADX (заголовок {orig_sfa[:4].hex()})")

    hdr_size = int.from_bytes(orig_sfa[2:4], "big") + 4
    header   = orig_sfa[:hdr_size]
    target   = len(orig_sfa) - hdr_size
    if target <= 0:
        raise RuntimeError("нулевая длина аудиоданных")

    work = TMP_ROOT / (sfd_path.stem + "_" + str(int(time.time() * 1000) % 100000))
    work.mkdir(parents=True, exist_ok=True)
    try:
        need_sec = target / (sr / 32 * 18 * ch) + 0.25
        wav = work / "new.wav"
        run([FFMPEG, "-y", "-i", wav_path, "-ar", sr, "-ac", ch,
             "-af", "apad", "-t", f"{need_sec:.3f}", "-c:a", "pcm_s16le", wav], cwd=work)

        adx_path = work / "new.adx"
        run([ADX_ENCODER, wav, adx_path], cwd=work)
        adx = adx_path.read_bytes()
        if adx[:2] != b"\x80\x00":
            raise RuntimeError("radx выдал не ADX")
        if int.from_bytes(adx[8:12], "big") != sr:
            raise RuntimeError("частота нового ADX не совпадает с оригиналом")
        if adx[4:8] != header[4:8]:
            header = header[:4] + adx[4:8] + header[8:]

        new_data = adx[int.from_bytes(adx[2:4], "big") + 4:]
        if len(new_data) < target:
            new_data += b"\x00" * (target - len(new_data))
        new_sfa = header + new_data[:target]
        if len(new_sfa) != len(orig_sfa):
            raise RuntimeError("длина нового SFA не совпала")

        out = bytearray(data)
        p = 0
        for s, e in chunks:
            L = e - s
            out[s:e] = new_sfa[p:p + L]
            p += L

        if len(out) != len(data):
            raise RuntimeError("размер файла изменился")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    chk  = probe(out_path)
    ca   = next((s for s in chk["streams"] if s["codec_type"] == "audio"), None)
    dur_n = float(chk["format"].get("duration", 0) or 0)
    if ca is None:
        raise RuntimeError("в результате нет аудио")
    if int(ca["sample_rate"]) != sr or int(ca["channels"]) != ch:
        raise RuntimeError("параметры аудио в результате не совпали")
    if dur_o and abs(dur_n - dur_o) > DUR_TOLERANCE:
        raise RuntimeError(f"длительность уехала: {dur_o:.2f} -> {dur_n:.2f} c")

    changed = sum(1 for s, e in chunks if data[s:e] != out[s:e])
    return dict(size=len(out), packets=len(chunks), changed=changed,
                dur_o=dur_o, dur_n=dur_n, codec=ca["codec_name"], sr=sr, ch=ch)

# ==================== ПАКЕТНЫЙ ПРОГОН ====================
def main():
    print("=" * 74)
    print(" ПАКЕТНАЯ ПОДМЕНА АУДИО В SFD (без пересборки контейнера)")
    print("=" * 74)

    if not ADX_ENCODER.is_file():
        print(f"[!] Не найден {ADX_ENCODER}"); sys.exit(1)
    for tool in (FFMPEG, FFPROBE):
        if shutil.which(tool) is None:
            print(f"[!] {tool} не найден в PATH"); sys.exit(1)

    plan = []
    for vdir_name, adir_name in PAIRS:
        vdir, adir = VIDEO_ROOT / vdir_name, AUDIO_ROOT / adir_name
        if not vdir.is_dir():
            print(f"[!] Нет папки видео: {vdir}"); continue
        if not adir.is_dir():
            print(f"[!] Нет папки озвучки: {adir} (все файлы будут пропущены)")
        idx = audio_index(adir)
        for sfd in sorted(vdir.rglob("*")):
            if not sfd.is_file() or sfd.suffix.lower() != ".sfd": continue
            rel = sfd.relative_to(vdir)
            wav = pick_audio(idx, adir, rel.parent, sfd.stem)
            out = OUT_ROOT / vdir_name / rel
            plan.append((sfd, wav, out, vdir_name))

    have = [x for x in plan if x[1]]
    miss = [x for x in plan if not x[1]]
    print(f"\nНайдено SFD: {len(plan)} | с озвучкой: {len(have)} | без озвучки (пропуск): {len(miss)}")
    print(f"Вывод: {OUT_ROOT}\n")

    if DRY_RUN:
        print("--- ПЛАН (dry-run, ничего не записывается) ---")
        for sfd, wav, out, grp in plan:
            tag = "OK  " if wav else "SKIP"
            print(f"[{tag}] {grp}/{sfd.relative_to(VIDEO_ROOT / grp)}"
                  f"  <-  {wav.name if wav else 'нет wav'}")
        return

    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    ok, skipped, failed, existed = [], [], [], []
    t0 = time.time()

    for i, (sfd, wav, out, grp) in enumerate(plan, 1):
        label = f"{grp}/{sfd.relative_to(VIDEO_ROOT / grp)}"
        print(f"[{i}/{len(plan)}] {label}")

        if wav is None:
            print("      SKIP: нет wav-файла")
            skipped.append((label, "нет wav"))
            continue
        if SKIP_EXISTING and not FORCE and out.exists() and out.stat().st_size == sfd.stat().st_size:
            print("      SKIP: уже готов (--force для переделки)")
            existed.append((label, str(out)))
            continue

        try:
            r = process(sfd, wav, out)
            print(f"      OK: {r['size']:,} Б | {r['codec']} {r['sr']}Гц/{r['ch']}кан | "
                  f"{r['dur_o']:.2f}->{r['dur_n']:.2f} c | пакетов {r['changed']}/{r['packets']}")
            ok.append((label, wav.name, r))
        except Exception as ex:
            print(f"      FAIL: {ex}")
            failed.append((label, str(ex)))

    shutil.rmtree(TMP_ROOT, ignore_errors=True)

    print("\n" + "=" * 74)
    print(f" ИТОГ за {time.time() - t0:.1f} c:  OK={len(ok)}  "
          f"уже было={len(existed)}  пропуск={len(skipped)}  ошибок={len(failed)}")
    print("=" * 74)
    if skipped:
        print("\nБез озвучки (не создавались):")
        for label, why in skipped: print(f"  {label}")
    if failed:
        print("\nОшибки:")
        for label, why in failed: print(f"  {label}: {why}")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rep = OUT_ROOT / "_sfd_report.txt"
    with rep.open("w", encoding="utf-8") as f:
        f.write("ОТЧЁТ ПО ПОДМЕНЕ АУДИО В SFD\n")
        f.write(f"OK={len(ok)}  уже было={len(existed)}  пропуск={len(skipped)}  ошибок={len(failed)}\n\n")
        for label, wn, r in ok:
            f.write(f"[OK]   {label} <- {wn} | {r['size']} Б | "
                    f"{r['dur_o']:.2f}->{r['dur_n']:.2f} c | {r['changed']}/{r['packets']} пакетов\n")
        for label, p in existed:
            f.write(f"[HAVE] {label} -> {p}\n")
        for label, why in skipped:
            f.write(f"[SKIP] {label} — {why}\n")
        for label, why in failed:
            f.write(f"[FAIL] {label} — {why}\n")
    print(f"\nОтчёт: {rep}")

if __name__ == "__main__":
    main()