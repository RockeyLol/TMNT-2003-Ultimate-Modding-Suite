import os, sys, json, subprocess, shutil
from pathlib import Path
from sfd_muxer.container import SFD

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR  = os.path.join(SCRIPT_DIR, "tools")
FFMPEG     = "ffmpeg"
FFPROBE    = "ffprobe"
ADX_ENCODER = os.path.join(TOOLS_DIR, "radx_encode.exe")

def run(cmd, desc, cwd=None):
    print(f"[*] {desc}")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"[!] ОШИБКА:\n{r.stderr or r.stdout}")
        sys.exit(1)
    if r.stdout.strip():
        print(f"    {r.stdout.strip()[:200]}")
    print("[+] OK")

def probe(path):
    r = subprocess.run([FFPROBE,"-v","quiet","-print_format","json",
                        "-show_streams","-show_format",path],
                       capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout)

def main():
    if len(sys.argv) < 4:
        print("Использование: python script.py <orig.sfd> <new.wav> <out.sfd>")
        sys.exit(1)

    orig_sfd = os.path.abspath(sys.argv[1])
    new_wav  = os.path.abspath(sys.argv[2])
    out_sfd  = os.path.abspath(sys.argv[3])

    work = os.path.join(SCRIPT_DIR, "temp_work")
    os.makedirs(work, exist_ok=True)

    print("="*70)
    print(" ЗАМЕНА АУДИО: оригинальный заголовок SFA + новый звук")
    print("="*70)

    # 1. Анализ оригинала
    info = probe(orig_sfd)
    a = next(s for s in info["streams"] if s["codec_type"]=="audio")
    sr, ch = a["sample_rate"], a["channels"]
    dur = info["format"].get("duration","9999")
    print(f"\n[1] Оригинал: {sr} Гц, {ch} кан., {float(dur):.2f} сек")

    # 2. Извлекаем оригинальное видео и эталонный SFA
    print("\n[2] Извлечение оригинала (видео + эталонный SFA)...")
    orig_video = os.path.join(work, "orig.m2v")
    orig_sfa   = os.path.join(work, "orig.sfa")
    run([sys.executable, "-m", "sfd_muxer", "demux", orig_sfd,
         "--video", orig_video, "--audio", orig_sfa], "demux оригинала")

    # 3. Подгоняем новый звук под параметры оригинала
    print("\n[3] Подгонка нового аудио...")
    wav = os.path.join(work, "new.wav")
    run([FFMPEG, "-y", "-i", new_wav,
         "-ar", str(sr), "-ac", str(ch), "-t", str(dur),
         "-c:a", "pcm_s16le", wav], "ffmpeg ресемплинг")

    # 4. Кодируем в ADX
    print("\n[4] Кодирование в ADX...")
    adx = os.path.join(work, "new.adx")
    run([ADX_ENCODER, wav, adx], "radx_encode")

    # 5. Собираем новый SFA: заголовок оригинала + данные нового ADX
    print("\n[5] Сборка нового SFA (заголовок оригинала + новый звук)...")
    sfa_bytes = Path(orig_sfa).read_bytes()
    adx_bytes = Path(adx).read_bytes()

    # Заголовок оригинального SFA: данные начинаются с 0x120
    HEADER_SIZE = 0x120
    header = sfa_bytes[:HEADER_SIZE]

    # В новом ADX данные начинаются после его собственного заголовка.
    # Читаем offset из байтов 2-3 (big-endian) + 4
    adx_data_offset = int.from_bytes(adx_bytes[2:4], "big") + 4
    new_audio_data = adx_bytes[adx_data_offset:]

    new_sfa = header + new_audio_data
    new_sfa_path = os.path.join(work, "new.sfa")
    Path(new_sfa_path).write_bytes(new_sfa)
    print(f"    Заголовок: {HEADER_SIZE} байт (оригинал)")
    print(f"    Новые аудио-данные: {len(new_audio_data)} байт")
    print(f"    Итого SFA: {len(new_sfa)} байт")

    # 6. Мультиплексируем через библиотеку
    print("\n[6] Сборка SFD...")
    video_bytes = Path(orig_video).read_bytes()
    audio_bytes = Path(new_sfa_path).read_bytes()
    out_tmp = os.path.join(work, "out.sfd")
    SFD.mux(video_bytes, audio_bytes).to_file(out_tmp)
    print("[+] Мультиплексирование завершено")

    # Перенос результата
    os.makedirs(os.path.dirname(out_sfd), exist_ok=True)
    shutil.move(out_tmp, out_sfd)

    s1 = os.path.getsize(orig_sfd)/1024/1024
    s2 = os.path.getsize(out_sfd)/1024/1024
    print(f"\n[+] ГОТОВО: {os.path.basename(out_sfd)}")
    print(f"    Оригинал: {s1:.2f} МБ → Результат: {s2:.2f} МБ")

    shutil.rmtree(work, ignore_errors=True)
    print("[+] Временные файлы удалены")

if __name__ == "__main__":
    main()