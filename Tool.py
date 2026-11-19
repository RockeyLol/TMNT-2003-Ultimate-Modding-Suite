#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TMNT 2003 Ultimate Modding Tool
AEX / AFS-DAT / TXD / PAC / Аудио   — без изменений
Текст (DAT)                         — полностью DatEditText.py (Редактор + Патч-реплейсер)
EXE                                 — ExeEditText.py (текст) + ExeTextureEdit.py (спрайты)

ВАЖНО: палитра и 32bpp-пиксели хранятся в порядке RGBA (НЕ BGRA).
"""
import os
import re
import sys
import json
import struct
import shutil
import queue
import hashlib
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from PIL import Image, ImageChops

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
AFSBUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AFSbuild.exe")
AUDIO_EXTS_IN = (".wav", ".mp3", ".flac")


# ------------------------- УТИЛИТЫ -------------------------
def safe_name(name):
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, '_')
    return name


def find_files_recursive(root, extensions):
    result = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(extensions):
                result.append(os.path.join(dirpath, fn))
    return result


class PngIndex(dict):
    """stem(lower) -> путь + ПОЛНАЯ карта папок (дубли имён не затираются)."""

    def __init__(self):
        super().__init__()
        self.folders = {}
        self.total = 0


def build_png_index(root_folder):
    index = PngIndex()
    if not os.path.isdir(root_folder):
        return index
    for dirpath, _, files in os.walk(root_folder):
        folder = os.path.basename(dirpath).lower()
        bucket = index.folders.setdefault(folder, {})
        for fn in files:
            if not fn.lower().endswith(".png"):
                continue
            stem = os.path.splitext(fn)[0].lower()
            full = os.path.join(dirpath, fn)
            index.total += 1
            if stem not in index:
                index[stem] = full
            if stem not in bucket:
                bucket[stem] = full
    return index


def txd_split_dup(stem):
    """'dojo__1' -> ('dojo', True); 'dojo_01' -> ('dojo_01', False)."""
    m = re.match(r'^(.+?)__(\d+)$', stem)
    if m:
        return m.group(1), True
    return stem, False


def txd_folder_map(png_index):
    cache = getattr(txd_folder_map, "_cache", None)
    if cache is not None and cache[0] is png_index:
        return cache[1]
    folders = getattr(png_index, "folders", None)
    if folders is None:
        folders = {}
        for k, v in png_index.items():
            folders.setdefault(os.path.basename(os.path.dirname(v)).lower(), {})[k] = v
    txd_folder_map._cache = (png_index, folders)
    return folders


def txd_make_finder(txd_path, png_index, dup_to_base=False):
    stem = os.path.splitext(os.path.basename(txd_path))[0].lower()
    base, is_dup = txd_split_dup(stem)
    folders = txd_folder_map(png_index)
    order = [stem, base] if (dup_to_base and is_dup) else [stem]
    index, use_local, folder = png_index, False, None
    for s in order:
        loc = folders.get(s)
        if loc:
            index, use_local, folder = loc, True, s
            break
    by_name = {}
    if use_local:
        for k, v in index.items():
            m = re.match(r'^(\d+)[ _\-](.+)$', k)
            if m:
                by_name.setdefault(m.group(2), []).append((int(m.group(1)), v))
            else:
                by_name.setdefault(k, []).append((10 ** 6, v))

    def find(first_no, tex_name):
        if not use_local:
            return None
        tl = (tex_name or "").lower()
        p = index.get("%02d_%s" % (first_no, tl))
        if p is None:
            cands = by_name.get(tl)
            if cands:
                p = min(cands, key=lambda x: abs(x[0] - first_no))[1]
        return p

    return find, use_local, folder, is_dup, len(index)


# ------------------------- AEX -------------------------
AEX_MAGIC = b'aexh\xf0\x1f\x00\x00idxn'
AEX_HDR_OFFSET = 0x2000
AEX_NAME_LEN = 16
AEX_ALIGN = 4
AEX_EXT = '.adx'


class AexTool:
    AEX_MAGIC = AEX_MAGIC

    @staticmethod
    def read_aex(path):
        with open(path, 'rb') as f:
            raw = f.read()
        if raw[:12] != AEX_MAGIC:
            raise ValueError('Это не .aex архив (неверная сигнатура)')
        col = struct.unpack_from('<I', raw, 0x0C)[0]
        names = []
        p = 0x10
        for _ in range(col):
            name = raw[p:p + AEX_NAME_LEN].split(b'\x00')[0].decode('utf-8', 'replace')
            names.append(name)
            p += AEX_NAME_LEN
        unclear, col2 = struct.unpack_from('>II', raw, AEX_HDR_OFFSET)
        if col2 != col:
            raise ValueError(f'col2={col2} != col={col} — формат не совпал')
        table = []
        p = AEX_HDR_OFFSET + 8
        for _ in range(col):
            off_rel, size = struct.unpack_from('>II', raw, p)
            p += 8
            table.append((off_rel, size))
        files = []
        gaps = set()
        prev_end = None
        for i, (off_rel, size) in enumerate(table):
            if prev_end is not None:
                gaps.add(off_rel - prev_end)
            abs_off = AEX_HDR_OFFSET + off_rel
            files.append(raw[abs_off:abs_off + size])
            prev_end = off_rel + size
        return {'names': names, 'unclear': unclear, 'files': files,
                'col': col, 'gaps': gaps, 'raw_size': len(raw)}

    @staticmethod
    def write_aex(path, arc, files=None, align=AEX_ALIGN):
        names = arc['names']
        unclear = arc['unclear']
        col = arc['col']
        files = files if files is not None else arc['files']
        if len(files) != col:
            raise ValueError(f'Число файлов {len(files)} != col {col}')
        if len(names) != col:
            raise ValueError(f'Число имён {len(names)} != col {col}')
        table = []
        off = 8 + col * 8
        for d in files:
            table.append((off, len(d)))
            end = off + len(d)
            if align:
                end = (end + align - 1) // align * align
            off = end
        out = bytearray()
        out += AEX_MAGIC
        out += struct.pack('<I', col)
        for n in names:
            b = n.encode('utf-8')[:AEX_NAME_LEN]
            out += b + b'\x00' * (AEX_NAME_LEN - len(b))
        if len(out) > AEX_HDR_OFFSET:
            raise ValueError('Имена файлов не влезают в 0x2000')
        out += b'\x00' * (AEX_HDR_OFFSET - len(out))
        out += struct.pack('>II', unclear, col)
        for off_rel, size in table:
            out += struct.pack('>II', off_rel, size)
        for i, ((off_rel, size), d) in enumerate(zip(table, files)):
            cur = len(out) - AEX_HDR_OFFSET
            if cur != off_rel:
                raise RuntimeError(f'Смещение {cur:#x} != ожидаемого {off_rel:#x}')
            out += d
            if align:
                pad = (-(off_rel + size)) % align
                if pad:
                    out += b'\x00' * pad
        with open(path, 'wb') as f:
            f.write(out)

    @staticmethod
    def unpack_all(aex_folder, output_folder, log):
        aex_files = find_files_recursive(aex_folder, ('.aex',))
        if not aex_files:
            log("Не найдено .aex файлов!")
            return 0, 0
        log(f"Найдено {len(aex_files)} .aex файлов\n")
        total = 0
        for aex_path in aex_files:
            rel = os.path.relpath(aex_path, aex_folder)
            log(f"--- {rel} ---")
            try:
                arc = AexTool.read_aex(aex_path)
            except Exception as e:
                log(f"  Пропущен: {e}")
                continue
            base = os.path.splitext(rel)[0]
            sub = os.path.join(output_folder, base)
            os.makedirs(sub, exist_ok=True)
            order = []
            used = {}
            for name, data in zip(arc['names'], arc['files']):
                b = name or 'unnamed'
                if b in used:
                    used[b] += 1
                    disk_base = f'{b}__{used[b]}'
                else:
                    used[b] = 0
                    disk_base = b
                fname = safe_name(disk_base) + AEX_EXT
                with open(os.path.join(sub, fname), 'wb') as f:
                    f.write(data)
                order.append(disk_base)
                total += 1
                log(f"  Извлечён: {disk_base}{AEX_EXT} ({len(data)} байт)")
            with open(os.path.join(sub, '_order.txt'), 'w', encoding='utf-8') as f:
                f.write(f'unclear={arc["unclear"]}\n')
                for n in order:
                    f.write(n + '\n')
            log(f"  Паддинги между файлами (уник.): {sorted(arc['gaps'])[:5]}")
        return total, len(aex_files)

    @staticmethod
    def patch_all(aex_folder, files_folder, output_folder, log,
                  import_audio=False, audio_folder=None, normalize_audio=True):
        aex_files = find_files_recursive(aex_folder, ('.aex',))
        if not aex_files:
            log("Не найдено .aex файлов!")
            return 0, 0
        os.makedirs(output_folder, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix='aex_audio_')
        total_rep = 0
        patched = 0
        try:
            for aex_path in aex_files:
                rel = os.path.relpath(aex_path, aex_folder)
                base = os.path.splitext(rel)[0]
                src_dir = os.path.join(files_folder, base)
                order_file = os.path.join(src_dir, '_order.txt')
                if not os.path.isfile(order_file):
                    log(f"{rel}: нет _order.txt в {src_dir}, пропуск")
                    continue
                log(f"--- {rel} ---")
                audio_map = {}
                if import_audio and audio_folder:
                    audio_src = os.path.join(audio_folder, base)
                    scan_root = audio_src if os.path.isdir(audio_src) else audio_folder
                    for dirpath, _, filenames in os.walk(scan_root):
                        for fn in filenames:
                            if fn.lower().endswith(AUDIO_EXTS_IN):
                                stem = os.path.splitext(fn)[0].lower()
                                audio_map[stem] = os.path.join(dirpath, fn)
                    log(f"  Аудио для архива: {len(audio_map)} файлов")
                try:
                    arc = AexTool.read_aex(aex_path)
                except Exception as e:
                    log(f"  Пропуск: {e}")
                    continue
                unclear = 0
                with open(order_file, 'r', encoding='utf-8') as f:
                    first = f.readline().strip()
                    if first.startswith('unclear='):
                        unclear = int(first.split('=', 1)[1])
                    order = [line.strip() for line in f if line.strip()]
                if len(order) != arc['col']:
                    log(f"  [!] _order.txt: {len(order)} строк, в архиве {arc['col']} файлов, пропуск")
                    continue
                files = []
                rep = 0
                mis = 0
                for idx, disk_base in enumerate(order):
                    if import_audio:
                        audio_path = audio_map.get(disk_base.lower())
                        if audio_path is None and '__' in disk_base:
                            audio_path = audio_map.get(disk_base.split('__')[0].lower())
                        if audio_path is not None:
                            out_adx = os.path.join(tmp_dir, safe_name(disk_base) + '.adx')
                            try:
                                if normalize_audio:
                                    cmd_ff = [FFMPEG, "-y", "-loglevel", "fatal",
                                              "-hide_banner", "-nostats", "-i", audio_path,
                                              "-ar", "24000", "-ac", "1",
                                              "-sample_fmt", "s16", out_adx]
                                else:
                                    cmd_ff = [FFMPEG, "-y", "-loglevel", "fatal",
                                              "-hide_banner", "-nostats",
                                              "-i", audio_path, out_adx]
                                subprocess.run(cmd_ff, check=True)
                                with open(out_adx, 'rb') as f:
                                    files.append(f.read())
                                log(f"  [OK] {disk_base}: импортирован из {os.path.basename(audio_path)}")
                                rep += 1
                                continue
                            except subprocess.CalledProcessError as e:
                                log(f"  [!] {disk_base}: ошибка ffmpeg: {e}")
                                mis += 1
                    src = os.path.join(src_dir, safe_name(disk_base) + AEX_EXT)
                    if not os.path.exists(src):
                        log(f"  [!] {disk_base}: нет файла, пропуск")
                        mis += 1
                        files.append(b'')
                        continue
                    with open(src, 'rb') as f:
                        files.append(f.read())
                out_path = os.path.join(output_folder, rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    AexTool.write_aex(out_path,
                                      {'names': arc['names'], 'unclear': unclear,
                                       'files': files, 'col': arc['col']},
                                      align=AEX_ALIGN)
                    patched += 1
                    log(f"  [OK] Пересобран: {rel} (заменено/импортировано {rep}, пропущено {mis})")
                    total_rep += rep
                except Exception as e:
                    log(f"  [!] Ошибка сборки: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return total_rep, patched


# ------------------------- AFS / DAT -------------------------
AFS_DUP_SEP = "__"


def afs_rel_path(name, index=0):
    rel = (name or "").replace("\\", os.sep).replace("/", os.sep)
    parts = [p for p in rel.split(os.sep) if p not in ("", ".", "..")]
    if not parts:
        parts = ["_unnamed_%04d.bin" % index]
    parts = [safe_name(p) for p in parts]
    return os.path.join(*parts)


def afs_disk_names(entries):
    used = set()
    result = []
    for i, (name, off, size) in enumerate(entries):
        rel = afs_rel_path(name, i)
        key = rel.lower()
        if key not in used:
            used.add(key)
            result.append(rel)
            continue
        base, ext = os.path.splitext(rel)
        n = 1
        while True:
            cand = "%s%s%d%s" % (base, AFS_DUP_SEP, n, ext)
            if cand.lower() not in used:
                used.add(cand.lower())
                result.append(cand)
                break
            n += 1
    return result


def afs_dup_info(entries):
    counts = {}
    for name, off, size in entries:
        k = (name or "").replace("/", "\\").lower()
        counts[k] = counts.get(k, 0) + 1
    return {k: v for k, v in counts.items() if v > 1}


class AfsTool:
    SECTOR = 2048

    @staticmethod
    def _align(v, a=None):
        a = a or AfsTool.SECTOR
        return (v + a - 1) // a * a

    @staticmethod
    def read_layout(afs_path):
        with open(afs_path, "rb") as f:
            raw = f.read()
        if raw[:4] != b'AFS\x00':
            return None
        col = struct.unpack_from("<I", raw, 4)[0]
        toc_end = 8 + col * 8
        if toc_end + 8 > len(raw):
            return None
        names_off, names_size = struct.unpack_from("<II", raw, toc_end)
        has_names = (names_off != 0)
        name_tab = names_off
        if not has_names:
            tab = 0
            while tab <= col * 48:
                tab += AfsTool.SECTOR
            name_tab = len(raw) - tab
        entries = []
        records = []
        for i in range(col):
            off, size = struct.unpack_from("<II", raw, 8 + i * 8)
            p = name_tab + i * 48
            rec = raw[p:p + 48]
            if len(rec) < 48:
                rec += b'\x00' * (48 - len(rec))
            name = rec[:44].split(b'\x00')[0].decode('utf8', 'replace').replace('\\\\', '\\')
            entries.append((name, off, size))
            records.append(rec)
        offs = [e[1] for e in entries if e[1] > 0]
        data_start = min(offs) if offs else AfsTool._align(toc_end + 8)
        if data_start < toc_end + 8:
            data_start = AfsTool._align(toc_end + 8)
        return {'raw': raw, 'col': col, 'toc_end': toc_end,
                'entries': entries, 'records': records,
                'names_off': names_off, 'names_size': names_size,
                'has_names': has_names, 'data_start': data_start}

    @staticmethod
    def read_structure(afs_path):
        lay = AfsTool.read_layout(afs_path)
        if lay is None:
            return None
        return lay['entries']

    @staticmethod
    def unpack_all(afs_folder, output_folder, log):
        files = find_files_recursive(afs_folder, ('.afs', '.dat'))
        if not files:
            log("Не найдено .afs/.dat файлов!")
            return 0, 0
        log("Найдено %d архивов" % len(files))
        total = 0
        total_dups = 0
        for path in files:
            rel = os.path.relpath(path, afs_folder)
            log("--- %s ---" % rel)
            lay = AfsTool.read_layout(path)
            if not lay or not lay['entries']:
                log("  Пропущен (не AFS)")
                continue
            entries = lay['entries']
            disk = afs_disk_names(entries)
            dups = afs_dup_info(entries)
            if dups:
                extra = sum(v - 1 for v in dups.values())
                total_dups += extra
                log("  [!] Повторяющихся имён: %d (лишних записей: %d) — суффиксы %s1, %s2, ..."
                    % (len(dups), extra, AFS_DUP_SEP, AFS_DUP_SEP))
            base = os.path.splitext(rel)[0]
            sub = os.path.join(output_folder, base)
            os.makedirs(sub, exist_ok=True)
            raw = lay['raw']
            for i, ((name, off, size), dname) in enumerate(zip(entries, disk)):
                data = raw[off:off + size]
                out_path = os.path.join(sub, dname)
                d = os.path.dirname(out_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(out_path, "wb") as o:
                    o.write(data)
                total += 1
                if dname != afs_rel_path(name, i):
                    log("  Извлечён: #%d '%s' -> %s (%d байт) [дубликат имени]"
                        % (i, name, dname, size))
                else:
                    log("  Извлечён: #%d '%s' (%d байт)" % (i, name, size))
        if total_dups:
            log("")
            log("Итого дубликатов: %d — все сохранены отдельными файлами." % total_dups)
        return total, len(files)

    @staticmethod
    def replace_all(afs_folder, files_folder, output_folder, log):
        files = find_files_recursive(afs_folder, ('.afs', '.dat'))
        if not files:
            log("Не найдено .afs/.dat!")
            return 0, 0
        os.makedirs(output_folder, exist_ok=True)
        total_rep = 0
        total_skip = 0
        for path in files:
            rel = os.path.relpath(path, afs_folder)
            base = os.path.splitext(rel)[0]
            unpack_dir = os.path.join(files_folder, base)
            if not os.path.isdir(unpack_dir):
                log("%s: нет папки %s с файлами, пропуск" % (rel, base))
                continue
            log("--- %s ---" % rel)
            lay = AfsTool.read_layout(path)
            if not lay or not lay['entries']:
                log("  Не AFS, пропуск")
                continue
            entries = lay['entries']
            disk = afs_disk_names(entries)
            if afs_dup_info(entries):
                log("  Есть одинаковые имена — каждая запись берёт свой файл по индексу")
            out_path = os.path.join(output_folder, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            shutil.copy2(path, out_path)
            rep = 0
            skip = 0
            with open(out_path, "r+b") as f:
                for i, ((name, off, size), dname) in enumerate(zip(entries, disk)):
                    src = os.path.join(unpack_dir, dname)
                    if not os.path.exists(src):
                        if dname != afs_rel_path(name, i):
                            log("  [!] #%d '%s': нет файла %s — пропуск" % (i, name, dname))
                            log("    Перераспакуй архив этой версией: раньше дубликаты перезаписывались.")
                        skip += 1
                        continue
                    new_size = os.path.getsize(src)
                    if new_size != size:
                        log("  [!] #%d '%s' (%s): размер %d != %d, пропуск"
                            % (i, name, dname, new_size, size))
                        skip += 1
                        continue
                    with open(src, "rb") as fsrc:
                        data = fsrc.read()
                    f.seek(off)
                    f.write(data)
                    rep += 1
                    log("  [OK] #%d '%s' <- %s: заменён" % (i, name, dname))
            total_rep += rep
            total_skip += skip
            if rep == 0:
                os.remove(out_path)
                log("  Нет замен, копия не создана")
            else:
                log("  Итого заменено: %d" % rep)
            log("")
        return total_rep, total_skip

    @staticmethod
    def rebuild_native(afs_folder, unpack_root_folder, output_folder, log):
        files = find_files_recursive(afs_folder, ('.afs', '.dat'))
        if not files:
            log("Не найдено .afs/.dat!")
            return 0
        os.makedirs(output_folder, exist_ok=True)
        total = 0
        for path in files:
            rel = os.path.relpath(path, afs_folder)
            base = os.path.splitext(rel)[0]
            unpack_dir = os.path.join(unpack_root_folder, base)
            if not os.path.isdir(unpack_dir):
                log("%s: нет папки %s, пропуск" % (rel, base))
                continue
            log("--- %s ---" % rel)
            lay = AfsTool.read_layout(path)
            if not lay or not lay['entries']:
                log("  Не AFS, пропуск")
                continue
            entries = lay['entries']
            col = lay['col']
            raw = lay['raw']
            disk = afs_disk_names(entries)
            pad_sizes = all(e[2] > 0 and e[2] % AfsTool.SECTOR == 0 for e in entries)
            if pad_sizes:
                log("  Размеры в оригинале кратны 2048 — сохраняю это правило")
            datas = []
            taken = 0
            for i, ((name, off, size), dname) in enumerate(zip(entries, disk)):
                src = os.path.join(unpack_dir, dname)
                if os.path.isfile(src):
                    with open(src, 'rb') as fs:
                        d = fs.read()
                    taken += 1
                    if len(d) != size:
                        log("  [~] #%d '%s' <- %s: %d -> %d байт"
                            % (i, name, dname, size, len(d)))
                else:
                    d = raw[off:off + size]
                    if dname != afs_rel_path(name, i):
                        log("  [!] #%d '%s': нет %s — взят оригинал" % (i, name, dname))
                if pad_sizes:
                    pad = (-len(d)) % AfsTool.SECTOR
                    if pad:
                        d += b'\x00' * pad
                datas.append(d)
            out = bytearray(raw[:lay['data_start']])
            if len(out) < lay['data_start']:
                out += b'\x00' * (lay['data_start'] - len(out))
            offsets = []
            for d in datas:
                offsets.append(len(out))
                out += d
                pad = (-len(out)) % AfsTool.SECTOR
                if pad:
                    out += b'\x00' * pad
            if lay['has_names']:
                names_off = len(out)
                names_size = max(lay['names_size'], col * 48)
                for rec in lay['records']:
                    out += rec
                pad = (-len(out)) % AfsTool.SECTOR
                if pad:
                    out += b'\x00' * pad
            else:
                names_off = 0
                names_size = 0
            struct.pack_into("<II", out, lay['toc_end'], names_off, names_size)
            for i, d in enumerate(datas):
                struct.pack_into("<II", out, 8 + i * 8, offsets[i], len(d))
            out_path = os.path.join(output_folder, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as fo:
                fo.write(out)
            total += 1
            log("  [OK] Пересобран: %s | файлов %d | с диска %d | %d -> %d байт"
                % (rel, col, taken, len(raw), len(out)))
        return total

    @staticmethod
    def rebuild_with_afsbuild(afs_folder, unpack_root_folder, output_folder, log):
        if not os.path.exists(AFSBUILD):
            log("AFSbuild.exe не найден: %s" % AFSBUILD)
            return 0
        files = find_files_recursive(afs_folder, ('.afs', '.dat'))
        if not files:
            log("Не найдено .afs/.dat!")
            return 0
        os.makedirs(output_folder, exist_ok=True)
        total = 0
        for path in files:
            rel = os.path.relpath(path, afs_folder)
            base = os.path.splitext(rel)[0]
            unpack_dir = os.path.join(unpack_root_folder, base)
            if not os.path.isdir(unpack_dir):
                log("%s: нет папки %s с распакованными файлами, пропуск" % (rel, base))
                continue
            out_path = os.path.join(output_folder, rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            log("--- %s ---" % rel)
            lay = AfsTool.read_layout(path)
            if lay and afs_dup_info(lay['entries']):
                log("  [!] В архиве есть одинаковые имена! AFSbuild берёт имена с диска,")
                log("    поэтому дубликаты (__1/__2) попадут внутрь с искажёнными именами.")
                log("    Используй кнопку «Пересобрать (встроенный)».")
            cmd = [AFSBUILD, "-b", unpack_dir, "-o", out_path, "-f", "1"]
            try:
                log("  Запуск: %s" % ' '.join(cmd))
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                if res.stdout.strip():
                    log("  %s" % res.stdout.strip())
                if res.stderr.strip():
                    log("  %s" % res.stderr.strip())
                total += 1
                log("  [OK] Пересобран: %s" % rel)
            except subprocess.CalledProcessError as e:
                log("  [!] Ошибка AFSbuild: %s" % e)
                if e.stdout:
                    log("    %s" % e.stdout)
                if e.stderr:
                    log("    %s" % e.stderr)
        return total


# ===================== TXD =====================
def txd_read_header(f):
    sig = f.read(4)
    if sig != b'\x23\x00\x00\x00':
        return None
    f.read(4)
    version = f.read(4)
    col = struct.unpack("<I", f.read(4))[0]
    return col, version


def txd_unpack_file(txd_path, out_root, log):
    with open(txd_path, "rb") as f:
        hdr = txd_read_header(f)
        if hdr is None:
            log("  ПРОПУСК (не TXD): " + os.path.basename(txd_path))
            return 0
        col, version = hdr
        txd_name = os.path.basename(txd_path)
        out_dir = os.path.join(out_root, txd_name[:-4])
        os.makedirs(out_dir, exist_ok=True)
        ss = 0
        for _ in range(col):
            name_block = f.read(64)
            tex_name = name_block.split(b"\x00")[0].decode("utf8", "replace").replace("\\\\", "\\")
            col_mip = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            for _ in range(col_mip):
                ss += 1
                f.read(24)
                w, h = struct.unpack("<II", f.read(8))
                bpp = struct.unpack("<I", f.read(4))[0]
                w_unclear = struct.unpack("<I", f.read(4))[0]
                w_eff = w
                if w + 2 == w_unclear:
                    w_eff += 2
                if bpp in (4, 8) and w == 1 and w_unclear == 4:
                    w_eff = 4
                out_path = os.path.join(out_dir, "%02d_%s.png" % (ss, tex_name))
                if bpp in (4, 8):
                    raw = f.read(w_eff * h)
                    img = Image.frombuffer('P', (w_eff, h), raw, 'raw', 'P', 0, 1)
                    col_pal = 16 if bpp == 4 else 256
                    pal = bytearray()
                    alpha = bytearray()
                    for _k in range(col_pal):
                        rgb = f.read(3)
                        a = f.read(1)[0]
                        pal += rgb
                        alpha.append(a)
                    img.putpalette(bytes(pal))
                    if any(a != 255 for a in alpha):
                        img.save(out_path, "PNG", transparency=bytes(alpha))
                    else:
                        img.save(out_path, "PNG")
                    img.close()
                    log("  #%02d '%s' %dx%d bpp=%d OK%s" %
                        (ss, tex_name, w_eff, h, bpp,
                         " (с альфой)" if any(a != 255 for a in alpha) else ""))
                elif bpp == 32:
                    raw = f.read(w_eff * h * 4)
                    img = Image.frombuffer('RGBA', (w_eff, h), raw, 'raw', 'RGBA', 0, 1)
                    img.save(out_path, "PNG")
                    img.close()
                    log("  #%02d '%s' %dx%d bpp=32 OK (RGBA)" % (ss, tex_name, w_eff, h))
                else:
                    log("  #%02d '%s' - НЕИЗВЕСТНЫЙ bpp=%d, остановка" % (ss, tex_name, bpp))
                    raise SystemExit(1)
        return ss


def txd_report_sizes(txd_path, png_index, log, dup_to_base=False):
    with open(txd_path, "rb") as f:
        data = f.read()
    if data[:4] != b'\x23\x00\x00\x00':
        log("  ПРОПУСК (не TXD): " + os.path.basename(txd_path))
        return 0, 0
    col = struct.unpack_from("<I", data, 12)[0]
    stem = os.path.splitext(os.path.basename(txd_path))[0].lower()
    find_png, use_local, folder, is_dup, n_files = txd_make_finder(txd_path, png_index, dup_to_base)
    if use_local:
        tag = " [дубликат -> базовая папка]" if (is_dup and folder != stem) else ""
        log("  PNG: папка '%s' (%d файлов)%s" % (folder, n_files, tag))
    pos = 16
    ss = 0
    diffs = 0
    for _ in range(col):
        if pos + 72 > len(data):
            break
        name_block = data[pos:pos + 64]
        pos += 64
        tex_name = name_block.split(b"\x00")[0].decode("utf8", "replace").replace("\\\\", "\\")
        col_mip = struct.unpack_from("<I", data, pos)[0]
        pos += 8
        base_no = ss + 1
        for mi in range(col_mip):
            ss += 1
            if pos + 40 > len(data):
                return diffs, ss
            pos += 24
            w, h, bpp, wu = struct.unpack_from("<IIII", data, pos)
            w_eff = w + 2 if w + 2 == wu else w
            if bpp in (4, 8) and w == 1 and wu == 4:
                w_eff = 4
            if bpp in (4, 8):
                plen = w_eff * h + (16 if bpp == 4 else 256) * 4
            elif bpp == 32:
                plen = w_eff * h * 4
            else:
                return diffs, ss
            if pos + 16 + plen > len(data):
                return diffs, ss
            pos += 16 + plen
            if mi:
                continue
            p = find_png(base_no, tex_name)
            if p is None:
                log("  #%02d '%s' %dx%d bpp=%d — PNG нет (останется оригинал)"
                    % (base_no, tex_name, w_eff, h, bpp))
                continue
            try:
                with Image.open(p) as im:
                    pw, ph = im.size
            except Exception as e:
                log("  #%02d '%s' — ошибка PNG: %s" % (base_no, tex_name, e))
                continue
            if (pw, ph) == (w_eff, h):
                log("  #%02d '%s' %dx%d bpp=%d — совпадает" % (base_no, tex_name, w_eff, h, bpp))
            else:
                diffs += 1
                pot = "" if (pw & (pw - 1)) == 0 and (ph & (ph - 1)) == 0 else "  [!] НЕ степень двойки!"
                log("  #%02d '%s' %dx%d -> PNG %dx%d  [!] РАЗНЫЕ%s"
                    % (base_no, tex_name, w_eff, h, pw, ph, pot))
    return diffs, ss


def txd_pack_file(txd_path, png_index, repack_root, log, allow_resize=False, fit_size=True, dup_to_base=False):
    """Упаковка PNG -> TXD, ТОЧЕЧНАЯ (сверка по хэшу RGBA, остальное байт-в-байт)."""

    def _payload_len(bpp, w, h):
        if bpp in (4, 8):
            return w * h + (16 if bpp == 4 else 256) * 4
        if bpp == 32:
            return w * h * 4
        return -1

    def _is_pot(n):
        return n > 0 and (n & (n - 1)) == 0

    def _to_rgba(im):
        if im.mode == "RGBA":
            return im
        if im.mode in ("P", "LA", "PA"):
            return im.convert("RGBA")
        trns = im.info.get("transparency")
        rgba = im.convert("RGBA")
        if isinstance(trns, (tuple, list)) and len(trns) >= 3:
            r, g, b, a = rgba.split()
            m = ImageChops.multiply(
                ImageChops.multiply(
                    r.point(lambda v, t=trns[0]: 255 if v == t else 0),
                    g.point(lambda v, t=trns[1]: 255 if v == t else 0)),
                b.point(lambda v, t=trns[2]: 255 if v == t else 0))
            rgba = Image.merge("RGBA", (r, g, b, ImageChops.subtract(a, m)))
        elif isinstance(trns, int):
            gray = im.convert("L")
            r, g, b, a = rgba.split()
            m = gray.point(lambda v, t=trns: 255 if v == t else 0)
            rgba = Image.merge("RGBA", (r, g, b, ImageChops.subtract(a, m)))
        return rgba

    def _resize_to(img, size):
        if img.size == size:
            return img
        mode = Image.NEAREST if img.mode == "P" else Image.LANCZOS
        return img.resize(size, mode)

    def _orig_rgba(orig, bpp, w, h):
        if bpp == 32:
            return Image.frombytes("RGBA", (w, h), orig[:w * h * 4])
        col_pal = 16 if bpp == 4 else 256
        idx = orig[:w * h]
        pal = orig[w * h:]
        rgb_lut = bytearray(768)
        a_lut = bytearray(256)
        for k in range(col_pal):
            rgb_lut[3 * k:3 * k + 3] = pal[4 * k:4 * k + 3]
            a_lut[k] = pal[4 * k + 3]
        p = Image.frombytes("P", (w, h), idx)
        p.putpalette(bytes(rgb_lut))
        rgb = p.convert("RGB")
        a = Image.frombytes("L", (w, h), idx.translate(bytes(a_lut)))
        return Image.merge("RGBA", (*rgb.split(), a))

    def _pal_bytes(entries, col_pal):
        out = bytearray()
        for k in range(col_pal):
            e = entries[k] if k < len(entries) else (0, 0, 0, 0)
            out += bytes(e)
        return bytes(out)

    def _pack_indexed(rgba, col_pal):
        """Индексы + палитра RGBA с сохранением градаций альфы (glow)."""
        w, h = rgba.size
        raw = rgba.tobytes()
        rs, gs, bs, as_ = raw[0::4], raw[1::4], raw[2::4], raw[3::4]
        hist = {}
        for c in zip(rs, gs, bs, as_):
            hist[c] = hist.get(c, 0) + 1
        has_tr = any(c[3] == 0 for c in hist)
        solid = {c: n for c, n in hist.items() if c[3] > 0}
        slots = max(1, col_pal - (1 if has_tr else 0))
        entries = [(0, 0, 0, 0)] if has_tr else []
        base_off = 1 if has_tr else 0
        if not solid:
            entries += [(0, 0, 0, 0)] * slots
            return bytes(w * h), entries[:col_pal], True
        if len(solid) <= slots:
            order = sorted(solid, key=lambda c: (c[3], c[0], c[1], c[2]))
            entries.extend(order)
            cmap = {c: base_off + i for i, c in enumerate(order)}
            if has_tr:
                for c in hist:
                    if c[3] == 0:
                        cmap[c] = 0
            idx = bytes(cmap[c] for c in zip(rs, gs, bs, as_))
            return idx, entries[:col_pal], True
        levels = {}
        for c, n in solid.items():
            levels.setdefault(c[3], []).append((c, n))
        a_keys = sorted(levels)
        max_groups = max(1, min(8, slots // 2))
        if len(a_keys) > max_groups:
            buckets = [[] for _ in range(max_groups)]
            for a in a_keys:
                buckets[min(max_groups - 1, a * max_groups // 256)].extend(levels[a])
            buckets = [b for b in buckets if b]
        else:
            buckets = [levels[a] for a in a_keys]
        groups = []
        for items in buckets:
            tot = sum(n for _, n in items)
            alev = sum(c[3] * n for c, n in items) // max(1, tot)
            groups.append((alev, items, tot))
        total_px = sum(g[2] for g in groups) or 1
        alloc = [max(1, int(slots * g[2] / total_px)) for g in groups]
        while sum(alloc) > slots:
            i = alloc.index(max(alloc))
            if alloc[i] <= 1:
                break
            alloc[i] -= 1
        while sum(alloc) < slots:
            i = max(range(len(groups)), key=lambda j: groups[j][2] / alloc[j])
            alloc[i] += 1
        ginfo = []
        for (alev, items, tot), k in zip(groups, alloc):
            uniq = sorted({(c[0], c[1], c[2]) for c, _ in items})
            if len(uniq) <= k:
                cols = uniq
            else:
                sample = Image.new("RGB", (len(uniq), 1))
                sample.putdata(uniq)
                q = sample.quantize(colors=k, method=Image.MEDIANCUT)
                pal = (q.getpalette() or [])[:k * 3]
                pal += [0] * (k * 3 - len(pal))
                cols = [(pal[3 * i], pal[3 * i + 1], pal[3 * i + 2]) for i in range(k)]
            start = len(entries)
            for c in cols:
                entries.append((c[0], c[1], c[2], alev))
            ginfo.append((start, cols, alev))
        alevs = [g[2] for g in ginfo]
        gid_lut = bytearray(256)
        for a in range(256):
            if a == 0 and has_tr:
                gid_lut[a] = 0
            else:
                gid_lut[a] = 1 + min(range(len(alevs)), key=lambda i: abs(alevs[i] - a))
        gid_img = Image.frombytes("L", (w, h), as_.translate(bytes(gid_lut)))
        rgb_img = rgba.convert("RGB")
        idxL = Image.new("L", (w, h), 0)
        for gi, (start, cols, alev) in enumerate(ginfo, 1):
            full, table = [], bytearray(256)
            for i in range(256):
                k = i % len(cols)
                full.extend(cols[k])
                table[i] = start + k
            ref = Image.new("P", (1, 1))
            ref.putpalette(full)
            q = rgb_img.quantize(palette=ref, dither=0)
            qL = Image.frombytes("L", (w, h), q.tobytes().translate(bytes(table)))
            mask = gid_img.point([255 if v == gi else 0 for v in range(256)])
            idxL.paste(qL, (0, 0, w, h), mask)
        return idxL.tobytes(), entries[:col_pal], False

    def _check_new_size(pw, ph, m0):
        if pw < 4 or ph < 4:
            return False, "меньше 4 пикселей"
        if pw > 4096 or ph > 4096:
            return False, "больше 4096 (лимит D3D8)"
        if _is_pot(m0['w_eff']) and not _is_pot(pw):
            return False, "ширина %d не степень двойки" % pw
        if _is_pot(m0['h_o']) and not _is_pot(ph):
            return False, "высота %d не степень двойки" % ph
        return True, ""

    def _new_hdr16(m, tw, th):
        w_o, w_u = m['w_o'], m['w_u']
        if w_o == 1 and w_u == 4 and tw == 4:
            nw, nu = 1, 4
        elif w_u == w_o + 2:
            nw, nu = max(1, tw - 2), tw
        else:
            nw, nu = tw, tw
        chunk = bytearray(m['hdr16'])
        struct.pack_into("<III", chunk, 0, nw, th, m['bpp'])
        struct.pack_into("<I", chunk, 12, nu)
        return bytes(chunk)

    def _patch_size_fields(block, m, tw, th, label):
        if len(block) < 4:
            return block
        bpp = m['bpp']
        px = 4 if bpp == 32 else 1
        pal = (16 if bpp == 4 else 256) * 4 if bpp in (4, 8) else 0
        ow, oh = m['w_eff'], m['h_o']
        cand = [
            (ow, tw, "ширина"),
            (oh, th, "высота"),
            (ow * oh, tw * th, "w*h"),
            (ow * px, tw * px, "stride"),
            (ow * oh * px, tw * th * px, "объём пикселей"),
            (ow * oh * px + pal, tw * th * px + pal, "объём данных"),
        ]
        cand = [(o, n, t) for o, n, t in cand if o != n and o >= 8]
        if not cand:
            return block
        buf = bytearray(block)
        for off in range(0, len(buf) - 3, 4):
            for endian, tag in (("<", "LE"), (">", "BE")):
                val = struct.unpack_from(endian + "I", buf, off)[0]
                hit = None
                for o, n, t in cand:
                    if val == o:
                        hit = (o, n, t)
                        break
                if hit:
                    struct.pack_into(endian + "I", buf, off, hit[1])
                    log("      %s: поле +%d (%s) %d -> %d [%s]"
                        % (label, off, hit[2], hit[0], hit[1], tag))
                    break
        return bytes(buf)

    # ------------------- чтение -------------------
    with open(txd_path, "rb") as f:
        data = f.read()
    if data[:4] != b'\x23\x00\x00\x00':
        log("  ПРОПУСК (не TXD): " + os.path.basename(txd_path))
        return 0, 0
    txd_name = os.path.basename(txd_path)
    version = data[8:12]
    col = struct.unpack_from("<I", data, 12)[0]

    # ------------------- 1. разбор структуры -------------------
    textures = []
    pos = 16
    running = 0
    truncated = False
    for _ in range(col):
        if pos + 72 > len(data):
            truncated = True
            break
        t_start = pos
        name_block = data[pos:pos + 64]
        pos += 64
        tex_name = name_block.split(b"\x00")[0].decode("utf8", "replace").replace("\\\\", "\\")
        col_mip = struct.unpack_from("<I", data, pos)[0]
        tail8 = data[pos:pos + 8]
        pos += 8
        mips = []
        for mi in range(col_mip):
            if pos + 40 > len(data):
                truncated = True
                break
            blk24 = data[pos:pos + 24]
            pos += 24
            hdr16 = data[pos:pos + 16]
            w_o, h_o, bpp, w_u = struct.unpack_from("<IIII", hdr16)
            w_eff = w_o + 2 if w_o + 2 == w_u else w_o
            if bpp in (4, 8) and w_o == 1 and w_u == 4:
                w_eff = 4
            plen = _payload_len(bpp, w_eff, h_o)
            if plen < 0 or pos + 16 + plen > len(data):
                pos -= 24
                truncated = True
                break
            orig = data[pos + 16:pos + 16 + plen]
            pos += 16 + plen
            mips.append({'mi': mi, 'blk24': blk24, 'hdr16': hdr16, 'orig': orig,
                         'w_o': w_o, 'h_o': h_o, 'bpp': bpp, 'w_u': w_u, 'w_eff': w_eff})
        running += len(mips)
        textures.append({'start': t_start, 'end': pos, 'name': tex_name,
                         'name_block': name_block, 'tail8': tail8, 'mips': mips,
                         'first_no': running - len(mips) + 1})
        if truncated:
            break
    ss_total = running
    if ss_total == 0:
        log("  Текстур не найдено — файл не трогаю")
        return 0, 0
    if truncated:
        log("  [!] структура распознана не полностью — неразобранная часть останется как есть")

    # ------------------- 2. свой индекс PNG -------------------
    txd_stem = os.path.splitext(txd_name)[0].lower()
    find_png, use_local, folder_label, is_dup, n_files = txd_make_finder(
        txd_path, png_index, dup_to_base)
    redirected = bool(use_local and is_dup and folder_label != txd_stem)
    if redirected:
        if allow_resize or not fit_size:
            log("  Дубликат имени: принудительно вписываю PNG под оригинальный размер")
        allow_resize = False
        fit_size = True
    pairs = [(t, find_png(t['first_no'], t['name'])) for t in textures]
    if not any(p for _, p in pairs):
        log("  Нет PNG для этого TXD — файл оставлен оригинальным (не создан)")
        return ss_total, ss_total
    if use_local:
        if redirected:
            log("  PNG: папка '%s' (%d файлов) — дубликат '%s' берёт базовую папку"
                % (folder_label, n_files, txd_name))
        else:
            log("  PNG: папка '%s' (%d файлов)" % (folder_label, n_files))
    else:
        log("  PNG: своей папки нет — файл оставлен оригинальным (не создан)")
        return ss_total, ss_total

    # ------------------- 3. собираем правки -------------------
    edits = []
    changed_mips = 0
    unchanged_tex = 0
    skipped_tex = 0
    resized = 0
    for t, png_path in pairs:
        label = "'%s'" % t['name']
        mips = t['mips']
        if not mips:
            continue
        if png_path is None:
            log("  #%02d %s - нет PNG, оригинал" % (t['first_no'], label))
            skipped_tex += len(mips)
            continue
        m0 = mips[0]
        target_w, target_h = m0['w_eff'], m0['h_o']
        mode = 'keep'
        src = None
        temps = []
        try:
            src = Image.open(png_path)
            src.load()
            pw, ph = src.size
            if (pw, ph) == (target_w, target_h):
                mode = 'asis'
            elif allow_resize:
                ok, why = _check_new_size(pw, ph, m0)
                if ok:
                    mode = 'resize'
                    target_w, target_h = pw, ph
                    resized += 1
                    log("  %s: RESIZE %dx%d -> %dx%d (экспериментально)"
                        % (label, m0['w_eff'], m0['h_o'], pw, ph))
                    log("      %s mip0 блок24: %s" % (label, m0['blk24'].hex().upper()))
                elif fit_size:
                    mode = 'fit'
                    log("  %s: менять размер нельзя (%s) — вписываю %dx%d -> %dx%d"
                        % (label, why, pw, ph, target_w, target_h))
                else:
                    log("  %s - ПРОПУЩЕНО (%s), оригинал" % (label, why))
            elif fit_size:
                mode = 'fit'
                log("  %s: вписано %dx%d -> %dx%d (размер файла не меняется)"
                    % (label, pw, ph, target_w, target_h))
            else:
                log("  %s - ПРОПУЩЕНО (размер %dx%d != %dx%d), оригинал"
                    % (label, pw, ph, target_w, target_h))
            if mode == 'keep':
                skipped_tex += len(mips)
                continue
            tail8 = t['tail8']
            if mode == 'resize':
                tail8 = _patch_size_fields(tail8, m0, target_w, target_h, label)
            parts = []
            for m in mips:
                if mode == 'resize':
                    tw = max(1, target_w >> m['mi'])
                    th = max(1, target_h >> m['mi'])
                else:
                    tw, th = m['w_eff'], m['h_o']
                work = _resize_to(src, (tw, th))
                if work is not src:
                    temps.append(work)
                rgba = _to_rgba(work)
                if rgba is not work:
                    temps.append(rgba)
                # --- СВЕРКА ПО ХЭШУ ---
                if mode != 'resize':
                    h_new = hashlib.md5(rgba.tobytes()).hexdigest()
                    try:
                        o = _orig_rgba(m['orig'], m['bpp'], tw, th)
                        h_old = hashlib.md5(o.tobytes()).hexdigest()
                        o.close()
                    except Exception:
                        h_old = None
                    if h_old is not None and h_new == h_old:
                        parts.append(m['blk24'] + m['hdr16'] + m['orig'])
                        log("  %s mip%d %dx%d — хэш совпал, пропуск"
                            % (label, m['mi'], tw, th))
                        continue
                if m['bpp'] == 32:
                    payload = rgba.tobytes()
                    old_a = set(m['orig'][3::4])
                    new_a = set(payload[3::4])
                else:
                    col_pal = 16 if m['bpp'] == 4 else 256
                    idx, entries, exact = _pack_indexed(rgba, col_pal)
                    payload = idx + _pal_bytes(entries, col_pal)
                    old_a = set(m['orig'][tw * th:][3::4])
                    new_a = set(payload[tw * th:][3::4])
                    log("  %s mip%d: палитра %s, уровней альфы %d -> %d"
                        % (label, m['mi'], "точная" if exact else "квантованная",
                           len(old_a), len(new_a)))
                if len(payload) != len(m['orig']):
                    log("  %s mip%d - размер данных %d != %d, оставляю оригинал"
                        % (label, m['mi'], len(payload), len(m['orig'])))
                    parts.append(m['blk24'] + m['hdr16'] + m['orig'])
                    continue
                if mode == 'resize':
                    hdr16 = _new_hdr16(m, tw, th)
                    blk24 = _patch_size_fields(m['blk24'], m, tw, th, label)
                else:
                    hdr16 = m['hdr16']
                    blk24 = m['blk24']
                semi_old = len([a for a in old_a if 0 < a < 255])
                semi_new = len([a for a in new_a if 0 < a < 255])
                if semi_new < semi_old:
                    log("  %s mip%d: [!] полупрозрачных градаций меньше (%d -> %d)"
                        % (label, m['mi'], semi_old, semi_new))
                parts.append(blk24 + hdr16 + payload)
                log("  %s mip%d %dx%d bpp=%d OK%s"
                    % (label, m['mi'], tw, th, m['bpp'],
                       " (альфа: %d град.)" % semi_new if semi_new else ""))
            new_block = t['name_block'] + tail8 + b"".join(parts)
            if new_block == data[t['start']:t['end']]:
                unchanged_tex += 1
                log("  %s — идентична оригиналу" % label)
            else:
                edits.append((t['start'], t['end'], new_block))
                changed_mips += len(mips)
        except Exception as e:
            log("  %s - ошибка (%s), текстура оставлена оригинальной" % (label, e))
            skipped_tex += len(mips)
        finally:
            for im in temps:
                try:
                    im.close()
                except Exception:
                    pass
            if src is not None:
                try:
                    src.close()
                except Exception:
                    pass

    # ------------------- 4. вклейка правок -------------------
    if not edits:
        log("  Изменений нет — файл НЕ создан (оригинал остаётся как есть)")
        return ss_total, ss_total
    out = bytearray(data)
    for start, end, block in sorted(edits, key=lambda e: e[0], reverse=True):
        out[start:end] = block
    if len(out) != len(data):
        struct.pack_into("<I", out, 4, len(out) - 12)
    if bytes(out) == data:
        log("  Итог совпал с оригиналом — файл НЕ создан")
        return ss_total, ss_total
    os.makedirs(repack_root, exist_ok=True)
    out_name = os.path.join(repack_root, txd_name)
    with open(out_name, "wb") as fo:
        fo.write(out)
    log("  ГОТОВО: %s | версия %s | текстур %d | изменено блоков %d | без изменений %d | пропущено %d | resize %d | размер %d"
        % (out_name, version.hex(), ss_total, len(edits), unchanged_tex,
           skipped_tex, resized, len(out)))
    if resized:
        log("  [!] Был RESIZE: размер файла изменился — только полная пересборка контейнера.")
    return ss_total, ss_total - changed_mips


# ===================== PAC =====================
class PacTool:
    @staticmethod
    def find_txd(pac_path):
        with open(pac_path, "rb") as f:
            data = f.read()
        offsets = []
        idx = 0
        while True:
            idx = data.find(b'\x23\x00\x00\x00', idx)
            if idx == -1:
                break
            if idx + 12 <= len(data):
                check = data[idx + 8:idx + 12]
                if check in (b'\xFF\xFF\x05\x10', b'\xFF\xFF\x03\x10'):
                    size = struct.unpack("<I", data[idx + 4:idx + 8])[0] + 12
                    offsets.append((idx, size))
            idx += 1
        return offsets

    @staticmethod
    def extract_all(pac_folder, output_folder, log):
        files = find_files_recursive(pac_folder, ('.pac',))
        if not files:
            log("Не найдено .pac файлов!")
            return 0
        os.makedirs(output_folder, exist_ok=True)
        total = 0
        for path in files:
            rel = os.path.relpath(path, pac_folder)
            log(f"--- {rel} ---")
            offsets = PacTool.find_txd(path)
            if not offsets:
                log("  TXD не найдены")
                continue
            rel_dir = os.path.dirname(rel)
            base = os.path.splitext(os.path.basename(path))[0]
            out_dir = os.path.join(output_folder, rel_dir)
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "rb") as f:
                for i, (off, size) in enumerate(offsets, 1):
                    f.seek(off)
                    data = f.read(size)
                    out = os.path.join(out_dir, f"{base}_{i:02d}.txd")
                    with open(out, "wb") as o:
                        o.write(data)
                    total += 1
                    log(f"  Извлечён: {os.path.basename(out)} ({size} байт)")
        return total

    @staticmethod
    def replace_txd(pac_path, txd_folder, output_path, log):
        offsets = PacTool.find_txd(pac_path)
        if not offsets:
            log(f"  {os.path.basename(pac_path)}: TXD не найдены")
            return 0
        if not os.path.isdir(txd_folder):
            log(f"  Нет папки с TXD: {txd_folder}")
            return 0
        base = os.path.splitext(os.path.basename(pac_path))[0]
        shutil.copy2(pac_path, output_path)
        replaced = 0
        with open(output_path, "r+b") as f:
            for i, (off, size) in enumerate(offsets, 1):
                want = f"{base}_{i:02d}.txd".lower()
                want2 = f"{base}_{i}.txd".lower()
                candidates = [
                    fn for fn in os.listdir(txd_folder)
                    if fn.lower() == want or fn.lower() == want2
                ]
                if not candidates:
                    continue
                txd_file = os.path.join(txd_folder, candidates[0])
                new_size = os.path.getsize(txd_file)
                if new_size != size:
                    log(f"  [!] TXD #{i} ({candidates[0]}): размер {new_size} != {size}, пропуск")
                    log("    (размер менялся — используй режим «Вписывать в оригинальный размер»)")
                    continue
                with open(txd_file, "rb") as tf:
                    new_data = tf.read()
                f.seek(off)
                f.write(new_data)
                replaced += 1
                log(f"  [OK] TXD #{i} ({candidates[0]}): заменён")
        if replaced == 0:
            os.remove(output_path)
            log("  Нет замен, копия не создана")
        return replaced


# ===================== AUDIO =====================
class AudioTool:
    @staticmethod
    def convert_folder(src_folder, dst_folder, out_ext, log):
        if not os.path.isdir(src_folder):
            log(f"Папка не найдена: {src_folder}")
            return 0
        if not os.path.exists(FFMPEG):
            log(f"ffmpeg.exe не найден: {FFMPEG}")
            return 0
        total = 0
        for dirpath, _, files in os.walk(src_folder):
            for fn in files:
                if not fn.lower().endswith(".adx"):
                    continue
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, src_folder)
                out_rel = os.path.splitext(rel)[0] + out_ext
                out_path = os.path.join(dst_folder, out_rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                log(f"  {rel} -> {out_rel}")
                try:
                    subprocess.run([FFMPEG, "-y", "-loglevel", "fatal", "-hide_banner",
                                    "-nostats", "-i", src, out_path], check=True)
                    total += 1
                except subprocess.CalledProcessError as e:
                    log(f"    [!] Ошибка ffmpeg: {e}")
        return total

    @staticmethod
    def convert_to_adx(src_folder, dst_folder, log, normalize_audio=True):
        if not os.path.isdir(src_folder):
            log(f"Папка не найдена: {src_folder}")
            return 0
        if not os.path.exists(FFMPEG):
            log(f"ffmpeg.exe не найден: {FFMPEG}")
            return 0
        total = 0
        for dirpath, _, files in os.walk(src_folder):
            for fn in files:
                if not fn.lower().endswith(AUDIO_EXTS_IN):
                    continue
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, src_folder)
                out_rel = os.path.splitext(rel)[0] + ".adx"
                out_path = os.path.join(dst_folder, out_rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                log(f"  {rel} -> {out_rel}")
                try:
                    if normalize_audio:
                        cmd_ff = [FFMPEG, "-y", "-loglevel", "fatal", "-hide_banner",
                                  "-nostats", "-i", src, "-ar", "24000", "-ac", "1",
                                  "-sample_fmt", "s16", out_path]
                    else:
                        cmd_ff = [FFMPEG, "-y", "-loglevel", "fatal", "-hide_banner",
                                  "-nostats", "-i", src, out_path]
                    subprocess.run(cmd_ff, check=True)
                    total += 1
                except subprocess.CalledProcessError as e:
                    log(f"    [!] Ошибка ffmpeg: {e}")
        return total


# ============================================================================
# ===============  ТЕКСТ .DAT  —  ПОЛНОСТЬЮ DatEditText.py  ==================
# ============================================================================
DAT_CHAR_MAP = {
    'A': 'А', 'B': 'Б', 'C': 'В', 'D': 'Г', 'E': 'Д',
    'F': 'Е', 'G': 'Ж', 'H': 'З', 'I': 'И', 'J': 'Й',
    'K': 'К', 'L': 'Л', 'M': 'М', 'N': 'Н', 'O': 'О',
    'P': 'П', 'Q': 'Р', 'R': 'С', 'S': 'Т', 'T': 'У',
    'U': 'Ф', 'V': 'Х', 'W': 'Ц', 'X': 'Ч', 'Y': 'Ш',
    'Z': 'Щ', '[': 'Ъ', '\\': 'Ы', ']': 'Ь',
    '^': 'Э', '_': 'Ю', '`': 'Я',
    'a': 'А', 'b': 'Б', 'c': 'В', 'd': 'Г', 'e': 'Д',
    'f': 'Е', 'g': 'Ж', 'h': 'З', 'i': 'И', 'j': 'Й',
    'k': 'К', 'l': 'Л', 'm': 'М', 'n': 'Н', 'o': 'О',
    'p': 'П', 'q': 'Р', 'r': 'С', 's': 'Т', 't': 'У',
    'u': 'Ф', 'v': 'Х', 'w': 'Ц', 'x': 'Ч', 'y': 'Ш',
    'z': 'Щ',
}
DAT_REV_MAP = {}
for _k, _v in DAT_CHAR_MAP.items():
    if _v not in DAT_REV_MAP:
        DAT_REV_MAP[_v] = _k
DAT_EXTRA = {'Ё': '~', 'ё': '~'}
DAT_NEWLINE_MARK = '⏎'


def dat_decode(s):
    return ''.join(DAT_CHAR_MAP.get(c, c) for c in s)


def dat_encode(s):
    s = s.replace('\r\n', '\n').replace('\r', '\n').upper()
    out = []
    for ch in s:
        if ch == '\n':
            out.append('\n')
        elif ch in DAT_REV_MAP:
            out.append(DAT_REV_MAP[ch])
        elif ch in DAT_EXTRA:
            out.append(DAT_EXTRA[ch])
        else:
            out.append(ch)
    return ''.join(out)


class DatReplica:
    def __init__(self, slot, p1, p2, raw, start, end):
        self.slot = slot
        self.p1 = p1
        self.p2 = p2
        self.raw = raw
        self.start = start
        self.end = end

    def original_cyr(self):
        return dat_decode(self.raw.decode('cp1251', errors='ignore'))


def dat_parse(data):
    replicas = []
    segments = []
    i = 0
    n = len(data)
    last = 0
    while i < n:
        if (i + 8 < n and data[i] == 0x05 and data[i + 1] == 0x09
                and data[i + 2] == 0x00):
            slot = data[i + 3]
            p1 = data[i + 4]
            p2 = data[i + 5]
            j = i + 6
            if j + 1 < n and data[j] == 0x09 and data[j + 1] == 0x01:
                j += 2
            ts = j
            while j < n and data[j] != 0x00:
                j += 1
            if j < n:
                raw = bytes(data[ts:j])
                rep = DatReplica(slot, p1, p2, raw, ts, j)
                replicas.append(rep)
                if i > last:
                    segments.append(('raw', bytes(data[last:i])))
                segments.append(('rep', rep))
                last = j + 1
                i = j + 1
                continue
        i += 1
    if last < n:
        segments.append(('raw', bytes(data[last:n])))
    return replicas, segments


def dat_build(orig_segs, new_texts):
    out = bytearray()
    rep_idx = 0
    total = sum(1 for k, _ in orig_segs if k == 'rep')

    def make_rep(slot, p1, text_bytes):
        p2 = (len(text_bytes) + 4) & 0xFF
        return (bytes([0x05, 0x09, 0x00, slot, p1, p2, 0x09, 0x01])
                + text_bytes + b'\x00')

    last_rep = None
    for kind, payload in orig_segs:
        if kind == 'raw':
            out.extend(payload)
        else:
            rep = payload
            last_rep = rep
            if rep_idx < len(new_texts) and new_texts[rep_idx] is not None:
                text = new_texts[rep_idx]
                tb = dat_encode(text).encode('cp1251', errors='replace')
            else:
                tb = rep.raw
            out.extend(make_rep(rep.slot, rep.p1, tb))
            rep_idx += 1
    if len(new_texts) > total and last_rep is not None:
        for k in range(total, len(new_texts)):
            nt = new_texts[k]
            if nt is None:
                continue
            tb = dat_encode(nt).encode('cp1251', errors='replace')
            out.extend(make_rep(last_rep.slot, last_rep.p1, tb))
            out.extend(b'\x03\x03\x00')
    return bytes(out)


def dat_parse_scene_txt(content):
    scenes = {}
    current = None
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = re.match(r'^СЦЕНА:\s*(.+?)\s*$', line)
        if m:
            current = m.group(1)
            scenes[current] = []
            continue
        if line.startswith('-') and set(line) <= {'-'}:
            continue
        if current is None:
            continue
        m = re.match(r'^(\d+)\s*[:.]\s?(.*)$', line)
        if m:
            num = int(m.group(1))
            text = m.group(2).replace(DAT_NEWLINE_MARK, '\n').strip()
            scenes[current].append((num, text))
    return scenes


def dat_export_scene_txt(scene_name, replicas):
    lines = []
    lines.append(f"СЦЕНА: {scene_name}")
    lines.append("-" * 40)
    for idx, rep in enumerate(replicas, 1):
        txt = rep.original_cyr().replace('\n', DAT_NEWLINE_MARK)
        lines.append(f"{idx}: {txt}")
        lines.append("")
    return "\n".join(lines)


# ======================= ВКЛАДКА 1: РЕДАКТОР =======================
class DatEditorTab:
    def __init__(self, parent):
        self.frame = tk.Frame(parent)
        self.orig_path = None
        self.orig_data = None
        self.orig_segs = None
        self.orig_reps = []
        self.items = []
        self.current = None
        self.max_len = tk.IntVar(value=127)
        self._sup = False
        self._ui()

    def _ui(self):
        top = tk.Frame(self.frame); top.pack(fill=tk.X, padx=8, pady=6)
        tk.Button(top, text="1. Открыть оригинал .dat", command=self.open_orig).pack(side=tk.LEFT)
        tk.Button(top, text="2. Загрузить TXT", command=self.load_txt).pack(side=tk.LEFT, padx=6)
        tk.Button(top, text="3. Сохранить новый .dat", command=self.save_dat).pack(side=tk.LEFT)
        self.path_lbl = tk.Label(top, text="(оригинал не выбран)", anchor='w')
        self.path_lbl.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        opt = tk.Frame(self.frame); opt.pack(fill=tk.X, padx=8)
        tk.Label(opt, text="p2 = len + 4 (фиксировано)").pack(side=tk.LEFT)
        tk.Label(opt, text="  макс. длина:").pack(side=tk.LEFT)
        tk.Entry(opt, textvariable=self.max_len, width=6).pack(side=tk.LEFT)
        tk.Label(opt, text="(0 = без лимита)").pack(side=tk.LEFT)

        main = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        left = tk.Frame(main); main.add(left, width=520)
        tk.Label(left, text="Реплики:").pack(anchor='w')
        lf = tk.Frame(left); lf.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(lf, activestyle='dotbox')
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(lf, command=self.listbox.yview); sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)
        lbtns = tk.Frame(left); lbtns.pack(fill=tk.X, pady=4)
        tk.Button(lbtns, text="+ реплика", command=self.add_item).pack(side=tk.LEFT)
        tk.Button(lbtns, text="− реплика", command=self.del_item).pack(side=tk.LEFT, padx=6)
        tk.Button(lbtns, text="↑", command=lambda: self.move_item(-1)).pack(side=tk.LEFT)
        tk.Button(lbtns, text="↓", command=lambda: self.move_item(1)).pack(side=tk.LEFT, padx=6)

        right = tk.Frame(main); main.add(right)
        tk.Label(right, text="Текст реплики (только ЗАГЛАВНЫЕ, ⏎ = перенос):").pack(anchor='w')
        self.info = tk.Label(right, text="", anchor='w', fg='gray'); self.info.pack(anchor='w')
        self.txt = tk.Text(right, height=10, wrap='word'); self.txt.pack(fill=tk.BOTH, expand=True, pady=4)
        self.txt.bind('<KeyRelease>', self._force_upper)
        self.txt.bind('<<Modified>>', self._on_modified)
        tk.Label(right, text="В файл ляжет:").pack(anchor='w')
        self.prev = tk.Text(right, height=5, wrap='word', state='disabled', bg='#eee')
        self.prev.pack(fill=tk.X)
        btns = tk.Frame(right); btns.pack(fill=tk.X, pady=6)
        tk.Button(btns, text="Применить (в память)", command=self.apply_current).pack(side=tk.LEFT)
        tk.Button(btns, text="Сброс к оригиналу", command=self.reset_current).pack(side=tk.LEFT, padx=6)
        self.status = tk.Label(self.frame, text="Готов", anchor='w')
        self.status.pack(fill=tk.X, padx=8, pady=(0, 6))

    def _on_modified(self, event=None):
        if self.txt.edit_modified():
            self.txt.edit_modified(False)
            self._force_upper()

    def _force_upper(self, event=None):
        if self._sup:
            return
        if event is not None and getattr(event, 'keysym', '') in (
                'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R',
                'Caps_Lock', 'Left', 'Right', 'Up', 'Down', 'Home', 'End',
                'Prior', 'Next', 'Escape', 'Tab'):
            return
        t = self.txt.get('1.0', 'end-1c'); u = t.upper()
        if t != u:
            self._sup = True
            cur = self.txt.index('insert')
            self.txt.delete('1.0', 'end'); self.txt.insert('1.0', u)
            self.txt.mark_set('insert', cur)
            self._sup = False
        self.update_preview()

    def open_orig(self):
        p = filedialog.askopenfilename(parent=self.frame,
                                       filetypes=[("DAT", "*.dat"), ("All", "*.*")])
        if not p:
            return
        with open(p, 'rb') as f:
            data = f.read()
        reps, segs = dat_parse(data)
        if not reps:
            messagebox.showwarning("Нет реплик", "В файле не найдено реплик", parent=self.frame)
            return
        self.orig_path = p
        self.orig_data = data
        self.orig_segs = segs
        self.orig_reps = reps
        self.items = []
        for r in reps:
            self.items.append({'slot': r.slot, 'p1': r.p1, 'p2': r.p2,
                               'orig_cyr': r.original_cyr(), 'cur_text': r.original_cyr()})
        self.path_lbl.config(text=p)
        self.refresh_list()
        self.status.config(text=f"Оригинал: {len(reps)} реплик, {len(data)} байт")

    def load_txt(self):
        if not self.items:
            messagebox.showwarning("Сначала оригинал", "Открой оригинальный .dat", parent=self.frame)
            return
        p = filedialog.askopenfilename(parent=self.frame,
                                       filetypes=[("TXT", "*.txt"), ("All", "*.*")])
        if not p:
            return
        with open(p, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        scenes = dat_parse_scene_txt(content)
        scene_name = os.path.splitext(os.path.basename(self.orig_path))[0]
        lines = scenes.get(scene_name)
        if lines is None:
            cand = [k for k in scenes if scene_name.lower() in k.lower()
                    or k.lower() in scene_name.lower()]
            if len(cand) == 1:
                lines = scenes[cand[0]]
            elif len(scenes) == 1:
                lines = scenes[next(iter(scenes))]
            else:
                dlg = tk.Toplevel(self.frame); dlg.title("Выбор сцены")
                dlg.geometry("400x300")
                tk.Label(dlg, text=f"Сцена '{scene_name}' не найдена. Выбери:").pack()
                lb = tk.Listbox(dlg); lb.pack(fill=tk.BOTH, expand=True)
                for k in scenes:
                    lb.insert(tk.END, k)
                res = {'v': None}

                def ok():
                    s = lb.curselection()
                    if s:
                        res['v'] = lb.get(s[0]); dlg.destroy()

                tk.Button(dlg, text="OK", command=ok).pack()
                dlg.transient(self.frame.winfo_toplevel()); dlg.grab_set()
                self.frame.wait_window(dlg)
                if not res['v']:
                    return
                lines = scenes[res['v']]
        lines.sort(key=lambda x: x[0])
        texts = [t for _, t in lines]
        while len(self.items) < len(texts):
            tpl = self.items[-1] if self.items else {'slot': 1, 'p1': 7, 'p2': 0}
            self.items.append({'slot': tpl['slot'], 'p1': tpl['p1'], 'p2': tpl['p2'],
                               'orig_cyr': '', 'cur_text': ''})
        for i in range(len(self.items)):
            self.items[i]['cur_text'] = texts[i].upper() if i < len(texts) else ''
        if len(texts) < len(self.items):
            self.items = self.items[:len(texts)]
        self.refresh_list()
        self.status.config(text=f"Загружено {len(texts)} реплик из TXT")

    def save_dat(self):
        if not self.orig_segs:
            messagebox.showwarning("Нет оригинала", "Сначала открой оригинал .dat", parent=self.frame)
            return
        if self.current is not None:
            self.apply_current()
        new_texts = [it['cur_text'] if it['cur_text'] != it['orig_cyr'] else None
                     for it in self.items]
        try:
            data = dat_build(self.orig_segs, new_texts)
        except Exception as e:
            messagebox.showerror("Ошибка сборки", str(e), parent=self.frame)
            return
        name = os.path.splitext(os.path.basename(self.orig_path))[0] + "_new.dat"
        out = filedialog.asksaveasfilename(parent=self.frame,
                                           initialdir=os.path.dirname(self.orig_path),
                                           initialfile=name, defaultextension=".dat",
                                           filetypes=[("DAT", "*.dat"), ("All", "*.*")])
        if not out:
            return
        with open(out, 'wb') as f:
            f.write(data)
        messagebox.showinfo("Готово", f"Сохранено:\n{out}\nРазмер: {len(data)} байт", parent=self.frame)
        self.status.config(text=f"Сохранено: {out} ({len(data)} байт)")

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        for i, it in enumerate(self.items, 1):
            t = it['cur_text'].replace('\n', ' ')
            if len(t) > 60:
                t = t[:57] + '...'
            mark = '*' if it['cur_text'] != it['orig_cyr'] else ' '
            self.listbox.insert(tk.END, f"{mark} {i:02d}: {t}")
        if self.current is not None and self.current < len(self.items):
            self.listbox.selection_set(self.current)

    def on_select(self, e=None):
        s = self.listbox.curselection()
        if not s:
            return
        self.load_index(s[0])

    def load_index(self, i):
        self.current = i
        it = self.items[i]
        self._sup = True
        self.txt.delete('1.0', 'end')
        self.txt.insert('1.0', it['cur_text'])
        self.txt.edit_modified(False)
        self._sup = False
        self.info.config(text=f"slot={it['slot']:02d} p1=0x{it['p1']:02X} p2=0x{it['p2']:02X}")
        self.update_preview()

    def update_preview(self):
        t = self.txt.get('1.0', 'end-1c')
        enc = dat_encode(t)
        self.prev.config(state='normal')
        self.prev.delete('1.0', 'end')
        self.prev.insert('1.0', enc.replace('\n', DAT_NEWLINE_MARK + '\n'))
        self.prev.config(state='disabled')

    def apply_current(self):
        if self.current is None:
            return
        self.items[self.current]['cur_text'] = self.txt.get('1.0', 'end-1c').rstrip('\n').upper()
        self.refresh_list()
        self.listbox.selection_set(self.current)

    def reset_current(self):
        if self.current is None:
            return
        it = self.items[self.current]
        it['cur_text'] = it['orig_cyr']
        self.load_index(self.current)
        self.refresh_list()

    def add_item(self):
        if not self.items:
            messagebox.showwarning("Нет данных", "Сначала открой оригинал", parent=self.frame)
            return
        tpl = self.items[-1]
        self.items.append({'slot': tpl['slot'], 'p1': tpl['p1'], 'p2': tpl['p2'],
                           'orig_cyr': '', 'cur_text': ''})
        self.refresh_list()
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(len(self.items) - 1)
        self.load_index(len(self.items) - 1)

    def del_item(self):
        if self.current is None:
            return
        del self.items[self.current]
        if not self.items:
            self.current = None
            self.txt.delete('1.0', 'end')
        else:
            self.current = min(self.current, len(self.items) - 1)
            self.load_index(self.current)
        self.refresh_list()

    def move_item(self, d):
        if self.current is None:
            return
        i = self.current; j = i + d
        if 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self.current = j
            self.refresh_list()
            self.listbox.selection_set(j)
            self.load_index(j)


# ======================= ВКЛАДКА 2: ПАТЧ-РЕПЛЕЙСЕР =======================
class DatPatchTab:
    def __init__(self, parent):
        self.frame = tk.Frame(parent)
        self.src_dir = tk.StringVar()
        self.txt_path = tk.StringVar()
        self.out_dir = tk.StringVar()
        self._ui()

    def _ui(self):
        f = tk.Frame(self.frame, padx=10, pady=10)
        f.pack(fill=tk.BOTH, expand=True)
        row1 = tk.Frame(f); row1.pack(fill=tk.X, pady=4)
        tk.Label(row1, text="1. Папка с оригинальными .dat:", width=32, anchor='w').pack(side=tk.LEFT)
        tk.Entry(row1, textvariable=self.src_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        tk.Button(row1, text="...", command=self.pick_src).pack(side=tk.LEFT)
        row2 = tk.Frame(f); row2.pack(fill=tk.X, pady=4)
        tk.Label(row2, text="2. TXT со сценарием:", width=32, anchor='w').pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.txt_path).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        tk.Button(row2, text="...", command=self.pick_txt).pack(side=tk.LEFT)
        row3 = tk.Frame(f); row3.pack(fill=tk.X, pady=4)
        tk.Label(row3, text="3. Папка для новых .dat:", width=32, anchor='w').pack(side=tk.LEFT)
        tk.Entry(row3, textvariable=self.out_dir).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        tk.Button(row3, text="...", command=self.pick_out).pack(side=tk.LEFT)
        btn_row = tk.Frame(f); btn_row.pack(fill=tk.X, pady=10)
        tk.Button(btn_row, text="ЭКСПОРТ ВСЕГО ТЕКСТА В TXT",
                  height=2, command=self.export_all).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(btn_row, text="ЗАПУСТИТЬ ЗАМЕНУ",
                  height=2, command=self.run).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.log = tk.Text(f, height=18, wrap='word')
        self.log.pack(fill=tk.BOTH, expand=True)

    def pick_src(self):
        p = filedialog.askdirectory(parent=self.frame, title="Папка с оригинальными .dat")
        if p:
            self.src_dir.set(p)

    def pick_txt(self):
        src = self.src_dir.get().strip()
        kw = {'initialdir': src} if src and os.path.isdir(src) else {}
        p = filedialog.askopenfilename(parent=self.frame, title="TXT со сценарием",
                                       filetypes=[("TXT", "*.txt"), ("All", "*.*")], **kw)
        if p:
            self.txt_path.set(p)

    def pick_out(self):
        p = filedialog.askdirectory(parent=self.frame, title="Папка для новых .dat")
        if p:
            self.out_dir.set(p)

    def _log(self, s):
        self.log.insert(tk.END, s + "\n")
        self.log.see(tk.END)
        self.frame.update_idletasks()

    def export_all(self):
        self.log.delete('1.0', tk.END)
        src = self.src_dir.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Укажи папку с оригинальными .dat", parent=self.frame)
            return
        files = sorted(f for f in os.listdir(src) if f.lower().endswith('.dat'))
        if not files:
            messagebox.showwarning("Пусто", "В папке нет .dat файлов", parent=self.frame)
            return
        out_path = filedialog.asksaveasfilename(parent=self.frame,
                                                title="Сохранить общий TXT",
                                                initialdir=src,
                                                initialfile="scenes_export.txt",
                                                defaultextension=".txt",
                                                filetypes=[("TXT", "*.txt"), ("All", "*.*")])
        if not out_path:
            return
        chunks = []
        total = 0
        for fn in files:
            fp = os.path.join(src, fn)
            try:
                with open(fp, 'rb') as f:
                    data = f.read()
                reps, _ = dat_parse(data)
                scene_name = os.path.splitext(fn)[0]
                chunks.append(dat_export_scene_txt(scene_name, reps))
                total += len(reps)
                self._log(f"[OK] {fn}: {len(reps)} реплик")
            except Exception as e:
                self._log(f"[!!] {fn}: {e}")
                chunks.append(f"СЦЕНА: {os.path.splitext(fn)[0]}\n# ОШИБКА: {e}\n\n")
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(chunks))
        except Exception as e:
            messagebox.showerror("Ошибка записи", str(e), parent=self.frame)
            return
        self._log("")
        self._log(f"Экспортировано файлов: {len(files)}, реплик: {total}")
        self._log(f"TXT: {out_path}")
        messagebox.showinfo("Готово",
                            f"Файлов: {len(files)}\nРеплик: {total}\n\n{out_path}", parent=self.frame)

    def run(self):
        self.log.delete('1.0', tk.END)
        src = self.src_dir.get().strip()
        txt = self.txt_path.get().strip()
        out = self.out_dir.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Укажи папку с оригинальными .dat", parent=self.frame)
            return
        if not txt or not os.path.isfile(txt):
            messagebox.showerror("Ошибка", "Укажи TXT со сценарием", parent=self.frame)
            return
        if not out:
            messagebox.showerror("Ошибка", "Укажи папку для новых .dat", parent=self.frame)
            return
        os.makedirs(out, exist_ok=True)
        try:
            with open(txt, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Ошибка чтения TXT", str(e), parent=self.frame)
            return
        scenes = dat_parse_scene_txt(content)
        self._log(f"Сцен в TXT: {len(scenes)}")
        files = sorted(f for f in os.listdir(src) if f.lower().endswith('.dat'))
        if not files:
            messagebox.showwarning("Пусто", "В папке нет .dat файлов", parent=self.frame)
            return
        ok = 0; skipped = 0; errors = 0
        for fn in files:
            scene_name = os.path.splitext(fn)[0]
            src_path = os.path.join(src, fn)
            lines = scenes.get(scene_name)
            if lines is None:
                cand = [k for k in scenes if scene_name.lower() in k.lower()
                        or k.lower() in scene_name.lower()]
                if len(cand) == 1:
                    lines = scenes[cand[0]]
                elif len(scenes) == 1:
                    lines = scenes[next(iter(scenes))]
                else:
                    self._log(f"[--] {fn}: сцена не найдена в TXT")
                    skipped += 1
                    continue
            try:
                with open(src_path, 'rb') as f:
                    data = f.read()
                reps, segs = dat_parse(data)
                if not reps:
                    self._log(f"[--] {fn}: реплик не найдено")
                    skipped += 1
                    continue
                lines.sort(key=lambda x: x[0])
                new_texts = []
                max_orig = len(reps)
                for idx in range(max_orig):
                    found = None
                    for num, t in lines:
                        if num == idx + 1:
                            found = t
                            break
                    new_texts.append(found if found is not None else None)
                extra = {}
                for num, t in lines:
                    if num > max_orig:
                        extra[num] = t
                for num in sorted(extra):
                    new_texts.append(extra[num])
                new_data = dat_build(segs, new_texts)
                out_path = os.path.join(out, fn)
                with open(out_path, 'wb') as f:
                    f.write(new_data)
                changed = sum(1 for i, r in enumerate(reps) if new_texts[i] is not None)
                added = len(new_texts) - max_orig
                self._log(f"[OK] {fn}: заменено {changed}/{max_orig}, "
                          f"добавлено {added}, размер {len(new_data)} байт")
                ok += 1
            except Exception as e:
                self._log(f"[!!] {fn}: {e}")
                errors += 1
        self._log("")
        self._log(f"Готово. Успешно: {ok}, пропущено: {skipped}, ошибок: {errors}")
        self._log(f"Новые файлы: {out}")
        messagebox.showinfo("Готово",
                            f"Успешно: {ok}\nПропущено: {skipped}\nОшибок: {errors}\n\n"
                            f"Новые .dat: {out}", parent=self.frame)


# ============================================================================
# ===============  EXE: ТЕКСТ  —  ExeEditText.py  ============================
# ============================================================================
EXE_CHAR_MAP = {
    'A': 'А', 'B': 'Б', 'C': 'В', 'D': 'Г', 'E': 'Д',
    'F': 'Е', 'G': 'Ж', 'H': 'З', 'I': 'И', 'J': 'Й',
    'K': 'К', 'L': 'Л', 'M': 'М', 'N': 'Н', 'O': 'О',
    'P': 'П', 'Q': 'Р', 'R': 'С', 'S': 'Т', 'T': 'У',
    'U': 'Ф', 'V': 'Х', 'W': 'Ц', 'X': 'Ч', 'Y': 'Ш',
    'Z': 'Щ', '[': 'Ъ', '\\': 'Ы', ']': 'Ь',
    '^': 'Э', '_': 'Ю', '`': 'Я',
}
EXE_REV_MAP = {}
for _k, _v in EXE_CHAR_MAP.items():
    if _v not in EXE_REV_MAP:
        EXE_REV_MAP[_v] = _k
EXE_NEWLINE_MARK = '\u23CE'
EXE_TOKEN_RE = re.compile(r'<[^<>]{1,24}>|%[sd]')


def exe_decode_text(s):
    return ''.join(EXE_CHAR_MAP.get(c, c) for c in s)


def exe_slot_text(slot):
    """RU — через шрифт-таблицу, EN/ID — как есть."""
    s = slot.raw.decode('ascii')
    return exe_decode_text(s) if slot.kind == 'ru' else s


def exe_encode_text(s):
    """Кириллица -> байтовая строка шрифта. Теги <...> и %s не трогаем."""
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    tokens = EXE_TOKEN_RE.findall(s)
    pieces = EXE_TOKEN_RE.split(s)
    res = []
    ti = 0
    for part in pieces:
        enc = ''.join('\n' if ch == '\n' else EXE_REV_MAP.get(ch, ch)
                      for ch in part.upper())
        res.append(enc)
        if ti < len(tokens):
            res.append(tokens[ti]); ti += 1
    return ''.join(res)


def exe_encode_for_slot(slot, text):
    if slot.kind == 'ru':
        return exe_encode_text(text)
    return text.replace('\r\n', '\n').replace('\r', '\n')


def exe_has_bad_chars(encoded):
    bad = set()
    for ch in encoded:
        if ch == '\n':
            continue
        if 32 <= ord(ch) <= 126:
            continue
        bad.add(ch)
    return bad


class ExeSlot:
    __slots__ = ('start', 'end', 'cap', 'raw', 'kind')

    def __init__(self, start, end, cap, raw, kind):
        self.start = start
        self.end = end
        self.cap = cap
        self.raw = raw
        self.kind = kind


def exe_classify(raw):
    try:
        s = raw.decode('ascii')
    except UnicodeDecodeError:
        return 'bin'
    if re.fullmatch(r'[a-z0-9_]+', s):
        return 'id'
    if any(c in s for c in '\\]^_`['):
        return 'ru'
    letters = [c for c in s if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return 'ru'
    return 'en'


def exe_parse(data, min_len=8):
    slots = []
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        if b == 0x0A or 32 <= b <= 126:
            j = i
            while j < n and (data[j] == 0x0A or 32 <= data[j] <= 126):
                j += 1
            if j < n and data[j] == 0x00 and (j - i) >= min_len:
                raw = data[i:j]
                kind = exe_classify(raw)
                if kind != 'bin':
                    k = j
                    while k < n and data[k] == 0x00:
                        k += 1
                    cap = k - i - 1
                    slots.append(ExeSlot(i, j, cap, raw, kind))
                    i = k
                    continue
            i = j + 1 if j > i else i + 1
        else:
            i += 1
    return slots


def exe_export_txt(path, slots, indexes):
    lines = ['# EXE text export',
             '# ⏎ = перенос строки (0A). Теги <clut=N> и %s не менять.', '']
    for idx in indexes:
        s = slots[idx]
        txt = exe_slot_text(s).replace('\n', EXE_NEWLINE_MARK)
        lines.append(f'[{idx}] ({s.kind}, {len(s.raw)}/{s.cap})')
        lines.append(txt)
        lines.append('')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def exe_import_txt(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()
    result = {}
    cur = None
    buf = []

    def flush():
        nonlocal cur, buf
        if cur is not None:
            t = '\n'.join(buf).strip('\n')
            result[cur] = t.replace(EXE_NEWLINE_MARK, '\n')
            cur, buf = None, []

    for line in content.splitlines():
        m = re.match(r'^\[(\d+)\]', line)
        if m:
            flush()
            cur = int(m.group(1))
            continue
        if line.startswith('#'):
            continue
        if cur is not None:
            buf.append(line)
    flush()
    return result


class ExeTextTab:
    """EXE Text Editor — фиксированные слоты (порт ExeEditText.py во вкладку)."""

    def __init__(self, parent):
        self.frame = tk.Frame(parent)
        self.path = None
        self.data = None
        self.slots = []
        self.view = []
        self.edits = {}
        self.current = None
        self.show_all = tk.BooleanVar(value=False)
        self.min_len = tk.IntVar(value=8)
        self._ui()

    def _ui(self):
        top = tk.Frame(self.frame); top.pack(fill=tk.X, padx=8, pady=6)
        tk.Button(top, text='1. Открыть .exe', command=self.open_exe).pack(side=tk.LEFT)
        tk.Button(top, text='Экспорт TXT', command=self.do_export).pack(side=tk.LEFT, padx=6)
        tk.Button(top, text='Импорт TXT', command=self.do_import).pack(side=tk.LEFT)
        tk.Button(top, text='2. Сохранить patched.exe', command=self.save_exe,
                  bg='#cfc').pack(side=tk.LEFT, padx=12)
        tk.Checkbutton(top, text='показать все строки (вкл. id/en)',
                       variable=self.show_all, command=self.refresh_list).pack(side=tk.LEFT, padx=10)
        tk.Label(top, text='мин. длина:').pack(side=tk.LEFT)
        tk.Entry(top, textvariable=self.min_len, width=4).pack(side=tk.LEFT)
        self.path_lbl = tk.Label(top, text='(файл не выбран)', anchor='w')
        self.path_lbl.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        main = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        left = tk.Frame(main); main.add(left, width=560)
        tk.Label(left, text='Строки (RU = перевод, * = изменено):').pack(anchor='w')
        lf = tk.Frame(left); lf.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(lf, activestyle='dotbox', font=('Consolas', 10))
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(lf, command=self.listbox.yview); sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)

        right = tk.Frame(main); main.add(right)
        self.info = tk.Label(right, text='', anchor='w', fg='gray'); self.info.pack(anchor='w')
        tk.Label(right, text='Оригинал (из файла):').pack(anchor='w')
        self.orig = tk.Text(right, height=7, wrap='word', state='disabled', bg='#f4f4f4')
        self.orig.pack(fill=tk.X)
        tk.Label(right, text='Твой текст (Enter = перенос, теги <clut=N>/%s сохраняй):').pack(anchor='w')
        self.txt = tk.Text(right, height=9, wrap='word')
        self.txt.pack(fill=tk.BOTH, expand=True, pady=4)
        self.txt.bind('<KeyRelease>', lambda e: self.update_preview())
        self.counter = tk.Label(right, text='', anchor='w', font=('Consolas', 11))
        self.counter.pack(anchor='w')
        tk.Label(right, text='Как ляжет в файл (шрифт-код):').pack(anchor='w')
        self.prev = tk.Text(right, height=5, wrap='word', state='disabled', bg='#eee')
        self.prev.pack(fill=tk.X)
        btns = tk.Frame(right); btns.pack(fill=tk.X, pady=6)
        tk.Button(btns, text='Применить (в память)', command=self.apply_current).pack(side=tk.LEFT)
        tk.Button(btns, text='Сброс к оригиналу', command=self.reset_current).pack(side=tk.LEFT, padx=6)
        self.status = tk.Label(self.frame, text='Ready', anchor='w')
        self.status.pack(fill=tk.X, padx=8, pady=(0, 6))

    def _min_len(self):
        try:
            return max(1, int(self.min_len.get()))
        except Exception:
            return 8

    def open_exe(self):
        p = filedialog.askopenfilename(parent=self.frame,
                                       filetypes=[('EXE', '*.exe'), ('All', '*.*')])
        if not p:
            return
        with open(p, 'rb') as f:
            data = f.read()
        slots = exe_parse(data, self._min_len())
        if not slots:
            messagebox.showwarning('No strings', 'No text strings found in this file', parent=self.frame)
            return
        self.path, self.data, self.slots = p, data, slots
        self.edits = {}
        self.current = None
        self.path_lbl.config(text=p)
        self.refresh_list()
        ru = sum(1 for s in slots if s.kind == 'ru')
        self.status.config(text=f'Loaded: {len(slots)} strings ({ru} look like RU translation), {len(data)} bytes')

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        self.view = []
        for idx, s in enumerate(self.slots):
            if not self.show_all.get() and s.kind != 'ru':
                continue
            self.view.append(idx)
            t = self.edits.get(idx)
            shown = (t if t is not None else exe_slot_text(s)).replace('\n', ' ')
            if len(shown) > 58:
                shown = shown[:55] + '...'
            mark = '*' if idx in self.edits else ' '
            self.listbox.insert(tk.END, f'{mark}[{idx:04d}] {s.kind.upper()} {len(s.raw):3d}/{s.cap:3d} {shown}')
        if self.current is not None and self.current in self.view:
            self.listbox.selection_set(self.view.index(self.current))

    def on_select(self, e=None):
        s = self.listbox.curselection()
        if s:
            self.load_index(self.view[s[0]])

    def load_index(self, idx):
        self.current = idx
        s = self.slots[idx]
        orig = exe_slot_text(s)
        self.orig.config(state='normal')
        self.orig.delete('1.0', 'end')
        self.orig.insert('1.0', orig)
        self.orig.config(state='disabled')
        self.txt.delete('1.0', 'end')
        self.txt.insert('1.0', self.edits.get(idx, orig))
        self.info.config(text=f'index={idx}  offset=0x{s.start:X}  kind={s.kind}  slot={s.cap} bytes (+1 terminator)')
        self.update_preview()

    def current_encoded(self):
        t = self.txt.get('1.0', 'end-1c')
        return exe_encode_for_slot(self.slots[self.current], t)

    def update_preview(self):
        if self.current is None:
            return
        s = self.slots[self.current]
        enc = self.current_encoded()
        try:
            nbytes = len(enc.encode('ascii'))
            bad = set()
        except UnicodeEncodeError:
            nbytes = len(enc)
            bad = exe_has_bad_chars(enc)
        free = s.cap - nbytes
        color = 'green' if free >= 0 and not bad else 'red'
        msg = f'bytes: {nbytes}/{s.cap}   free: {free}'
        if bad:
            msg += '   UNSUPPORTED CHARS: ' + ' '.join(sorted(bad)) + '  (use Е instead of Ё etc.)'
        if free < 0:
            msg += '   TOO LONG - shorten the text!'
        self.counter.config(text=msg, fg=color)
        self.prev.config(state='normal')
        self.prev.delete('1.0', 'end')
        self.prev.insert('1.0', enc.replace('\n', EXE_NEWLINE_MARK + '\n'))
        self.prev.config(state='disabled')

    def apply_current(self):
        if self.current is None:
            return
        s = self.slots[self.current]
        enc = self.current_encoded()
        try:
            b = enc.encode('ascii')
        except UnicodeEncodeError:
            messagebox.showerror('Bad characters',
                                 'Text contains characters missing in the game font.\n'
                                 'Unsupported: ' + ' '.join(sorted(exe_has_bad_chars(enc))),
                                 parent=self.frame)
            return
        if len(b) > s.cap:
            messagebox.showerror('Too long',
                                 f'Text is {len(b)} bytes, slot holds only {s.cap}.\n'
                                 f'Shorten by {len(b) - s.cap} bytes.', parent=self.frame)
            return
        t = self.txt.get('1.0', 'end-1c')
        if t == exe_slot_text(s):
            self.edits.pop(self.current, None)
        else:
            self.edits[self.current] = t
        self.refresh_list()
        self.status.config(text=f'Applied. Total edits pending: {len(self.edits)}')

    def reset_current(self):
        if self.current is None:
            return
        self.edits.pop(self.current, None)
        self.load_index(self.current)
        self.refresh_list()

    def do_export(self):
        if not self.slots:
            messagebox.showwarning('No file', 'Open .exe first', parent=self.frame)
            return
        p = filedialog.asksaveasfilename(
            parent=self.frame, defaultextension='.txt',
            initialfile=os.path.splitext(os.path.basename(self.path))[0] + '_text.txt',
            filetypes=[('TXT', '*.txt')])
        if not p:
            return
        idxs = self.view if self.view else list(range(len(self.slots)))
        exe_export_txt(p, self.slots, idxs)
        self.status.config(text=f'Exported {len(idxs)} strings -> {p}')

    def do_import(self):
        if not self.slots:
            messagebox.showwarning('No file', 'Open .exe first', parent=self.frame)
            return
        p = filedialog.askopenfilename(parent=self.frame, filetypes=[('TXT', '*.txt')])
        if not p:
            return
        texts = exe_import_txt(p)
        applied, errors = 0, []
        for idx, t in texts.items():
            if idx >= len(self.slots):
                errors.append(f'[{idx}] index out of range'); continue
            s = self.slots[idx]
            enc = exe_encode_for_slot(s, t)
            try:
                b = enc.encode('ascii')
            except UnicodeEncodeError:
                errors.append(f'[{idx}] unsupported chars: ' + ' '.join(sorted(exe_has_bad_chars(enc))))
                continue
            if len(b) > s.cap:
                errors.append(f'[{idx}] too long: {len(b)}/{s.cap}')
                continue
            if t != exe_slot_text(s):
                self.edits[idx] = t
                applied += 1
        self.refresh_list()
        report = f'Imported: {applied} edits applied.'
        if errors:
            report += '\n\nProblems:\n' + '\n'.join(errors)
            messagebox.showwarning('Import done with problems', report, parent=self.frame)
        else:
            messagebox.showinfo('Import', report, parent=self.frame)
        self.status.config(text=f'Import finished: {applied} applied, {len(errors)} problems')

    def save_exe(self):
        if not self.data:
            messagebox.showwarning('No file', 'Open .exe first', parent=self.frame)
            return
        if self.current is not None:
            self.apply_current()
        if not self.edits:
            messagebox.showinfo('Nothing to save', 'No edits applied', parent=self.frame)
            return
        out = filedialog.asksaveasfilename(
            parent=self.frame,
            initialdir=os.path.dirname(self.path),
            initialfile=os.path.splitext(os.path.basename(self.path))[0] + '_patched.exe',
            defaultextension='.exe', filetypes=[('EXE', '*.exe'), ('All', '*.*')])
        if not out:
            return
        buf = bytearray(self.data)
        for idx, t in self.edits.items():
            s = self.slots[idx]
            b = exe_encode_for_slot(s, t).encode('ascii')
            assert len(b) <= s.cap
            buf[s.start:s.start + s.cap + 1] = b + b'\x00' * (s.cap + 1 - len(b))
        assert len(buf) == len(self.data)      # размер файла не меняется!
        bak = self.path + '.bak'
        if not os.path.exists(bak):
            shutil.copy2(self.path, bak)
        with open(out, 'wb') as f:
            f.write(buf)
        messagebox.showinfo('Saved',
                            f'Patched file: {out}\nEdits: {len(self.edits)}\n'
                            f'Size unchanged: {len(buf)} bytes\nBackup: {bak}', parent=self.frame)
        self.status.config(text=f'Saved: {out} ({len(self.edits)} edits, size unchanged)')


# ============================================================================
# ===============  EXE: СПРАЙТЫ  —  ExeTextureEdit.py  =======================
# ============================================================================
SP_IDENT = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
SP_SCAN_SECS = (".rdata", ".data")
SP_MAX_DIM = 4096
SP_MAX_POS = 8192
# id, размер записи, смещение ptr, поля: (смещение, тип, имя)
SP_LAYOUTS = [
    ("I24p20", 24, 20, [(0, 'i', 'x'), (4, 'i', 'y'), (8, 'I', 'w'), (12, 'I', 'h'), (16, 'I', 'flags')]),
    ("F24p4",  24,  4, [(0, 'I', 'flags'), (8, 'f', 'x'), (12, 'f', 'y'), (16, 'f', 'w'), (20, 'f', 'h')]),
    ("I24p0",  24,  0, [(4, 'i', 'x'), (8, 'i', 'y'), (12, 'I', 'w'), (16, 'I', 'h'), (20, 'I', 'flags')]),
    ("F24p20", 24, 20, [(0, 'f', 'x'), (4, 'f', 'y'), (8, 'f', 'w'), (12, 'f', 'h'), (16, 'I', 'flags')]),
]
SP_LAY = {l[0]: l for l in SP_LAYOUTS}
SP_COLS = ["rec_off", "rec_va", "layout", "name", "x", "y", "w", "h", "flags",
           "tex_w", "tex_h", "bpp", "scale", "score", "ptr_off"]


def sp_pe_info(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\x00\x00":
        raise ValueError("не PE-файл")
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    base = struct.unpack_from("<I", data, opt + 28)[0] if magic == 0x10b else 0x400000
    tab = opt + optsz
    secs = []
    for i in range(nsec):
        p = tab + i * 40
        nm = data[p:p + 8].split(b"\x00")[0].decode("ascii", "replace")
        vsz, va, rsz, rp = struct.unpack_from("<IIII", data, p + 8)
        secs.append((nm, rp, rsz, va, vsz))
    return base, secs


def sp_off2va(off, base, secs):
    for nm, rp, rsz, va, vsz in secs:
        if rp <= off < rp + rsz:
            return base + va + (off - rp)
    return None


def sp_va2off(va, base, secs, size):
    for nm, rp, rsz, vaddr, vsz in secs:
        if base + vaddr <= va < base + vaddr + max(rsz, vsz):
            o = rp + (va - base - vaddr)
            if 0 <= o < size:
                return o
    return None


def sp_sec_of(off, secs):
    for nm, rp, rsz, va, vsz in secs:
        if rp <= off < rp + rsz:
            return nm
    return None


def sp_in_scan(off, secs):
    return sp_sec_of(off, secs) in SP_SCAN_SECS


def sp_txd_names(root):
    """name.lower() -> (w, h, bpp) по mip0."""
    out = {}
    for dp, _, fs in os.walk(root):
        for fn in fs:
            if not fn.lower().endswith(".txd"):
                continue
            try:
                with open(os.path.join(dp, fn), "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            if data[:4] != b"\x23\x00\x00\x00":
                continue
            col = struct.unpack_from("<I", data, 12)[0]
            pos = 16
            for _ in range(col):
                if pos + 72 > len(data):
                    break
                nm = data[pos:pos + 64].split(b"\x00")[0].decode("utf8", "replace")
                nm = nm.replace("\\\\", "\\")
                pos += 64
                cm = struct.unpack_from("<I", data, pos)[0]
                pos += 8
                first = None
                bad = False
                for mi in range(cm):
                    if pos + 40 > len(data):
                        bad = True
                        break
                    pos += 24
                    w, h, bpp, wu = struct.unpack_from("<IIII", data, pos)
                    we = w + 2 if w + 2 == wu else w
                    if bpp in (4, 8) and w == 1 and wu == 4:
                        we = 4
                    if bpp in (4, 8):
                        pl = we * h + (16 if bpp == 4 else 256) * 4
                    elif bpp == 32:
                        pl = we * h * 4
                    else:
                        bad = True
                        break
                    if pos + 16 + pl > len(data):
                        bad = True
                        break
                    pos += 16 + pl
                    if mi == 0:
                        first = (we, h, bpp)
                if bad:
                    break
                if first and nm:
                    out.setdefault(nm.lower(), first)
    return out


def sp_collect_names(data, base, secs, wanted):
    pat = re.compile(rb"[A-Za-z][A-Za-z0-9_\-]{2,39}\x00")
    out = {}
    for m in pat.finditer(data):
        s = m.start()
        if s > 0 and data[s - 1] in SP_IDENT:
            continue
        if not sp_in_scan(s, secs):
            continue
        nm = m.group()[:-1].decode("ascii")
        if wanted is not None and nm.lower() not in wanted:
            continue
        va = sp_off2va(s, base, secs)
        if va:
            out[va] = nm
    return out


def sp_decode(layout, data, start):
    _, size, ptroff, fields = layout
    vals = {}
    for off, t, nm in fields:
        vals[nm] = struct.unpack_from("<" + t, data, start + off)[0]
    return vals


def sp_plausible(v, dim):
    if dim:
        return 0 < v <= SP_MAX_DIM
    return abs(v) <= SP_MAX_POS


def sp_score(vals, tex):
    w, h, fl = vals['w'], vals['h'], vals['flags']
    if not (sp_plausible(w, True) and sp_plausible(h, True)):
        return -1
    if not (sp_plausible(vals['x'], False) and sp_plausible(vals['y'], False)):
        return -1
    if isinstance(fl, int) and fl > 0xFFFF:
        return -1
    s = 1
    if isinstance(fl, int) and fl in (0, 1, 2, 3, 4, 5, 8, 16, 28):
        s += 1
    if tex:
        tw, th, _ = tex
        for k in (1.0, 0.5, 2.0, 0.25, 4.0):
            if abs(w - tw * k) <= 1 and abs(h - th * k) <= 1:
                s += 4
                break
    else:
        if float(w).is_integer() and float(h).is_integer():
            s += 1
    return s


def sp_find_records(data, base, secs, names, texmap, min_score):
    hits = {}
    for nm, rp, rsz, va, vsz in secs:
        if nm not in SP_SCAN_SECS:
            continue
        end = min(rp + rsz, len(data) - 4)
        for p in range(rp, end, 4):
            v = struct.unpack_from("<I", data, p)[0]
            texname = names.get(v)
            if texname is None:
                continue
            tex = texmap.get(texname.lower()) if texmap else None
            for layout in SP_LAYOUTS:
                lid, size, ptroff, _ = layout
                start = p - ptroff
                if start < 0 or start + size > len(data):
                    continue
                if not sp_in_scan(start, secs):
                    continue
                try:
                    vals = sp_decode(layout, data, start)
                except struct.error:
                    continue
                sc = sp_score(vals, tex)
                if sc < min_score:
                    continue
                old = hits.get(start)
                if old and old['score'] >= sc:
                    continue
                hits[start] = dict(rec_off=start, layout=lid, name=texname,
                                   ptr_off=p, score=sc, vals=vals, tex=tex)
    return [hits[k] for k in sorted(hits)]


def sp_export(exe, txd_dir, out_tsv, min_score, want_all, log):
    with open(exe, "rb") as f:
        data = f.read()
    base, secs = sp_pe_info(data)
    texmap = sp_txd_names(txd_dir) if txd_dir else None
    if texmap is not None:
        log("Имён текстур из TXD: %d" % len(texmap))
    wanted = set(texmap.keys()) if (texmap and not want_all) else None
    names = sp_collect_names(data, base, secs, wanted)
    log("Строк-кандидатов в exe: %d" % len(names))
    recs = sp_find_records(data, base, secs, names, texmap, min_score)
    log("Найдено записей спрайтов: %d" % len(recs))
    if not recs:
        log("Ничего не найдено. Попробуй --all или min-score 1")
        return
    lines = ["\t".join(SP_COLS)]
    lock = {}
    for r in recs:
        v = r['vals']
        tex = r['tex']
        tw, th, bpp = tex if tex else ("", "", "")
        scale = ""
        if tex and tex[0]:
            scale = "%.4g" % (v['w'] / float(tex[0]))
        va = sp_off2va(r['rec_off'], base, secs) or 0

        def fmt(x):
            return ("%g" % x) if isinstance(x, float) else str(x)

        row = ["0x%08X" % r['rec_off'], "0x%08X" % va, r['layout'], r['name'],
               fmt(v['x']), fmt(v['y']), fmt(v['w']), fmt(v['h']), fmt(v['flags']),
               str(tw), str(th), str(bpp), scale, str(r['score']),
               "0x%08X" % r['ptr_off']]
        lines.append("\t".join(row))
        lock["0x%08X" % r['rec_off']] = dict(
            name=r['name'], layout=r['layout'],
            raw=data[r['rec_off']:r['rec_off'] + SP_LAY[r['layout']][1]].hex())
    with open(out_tsv, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    with open(out_tsv + ".lock.json", "w", encoding="utf-8") as f:
        json.dump(lock, f, ensure_ascii=False, indent=1)
    log("")
    log("Таблица: %s" % out_tsv)
    log("Снапшот: %s" % (out_tsv + ".lock.json"))
    log("")
    log("Первые строки:")
    for l in lines[:12]:
        log("  " + l)
    matched = sum(1 for r in recs if r['tex'])
    log("")
    log("С размерами из TXD: %d / %d" % (matched, len(recs)))
    log("Правь колонки x, y, w, h, flags. Колонки rec_off/layout/name не трогай.")


def sp_num(s):
    s = str(s).strip().replace(",", ".")
    if s == "":
        return None
    return float(s)


def sp_import(exe, tsv, out_exe, dry, log):
    with open(exe, "rb") as f:
        data = bytearray(f.read())
    base, secs = sp_pe_info(bytes(data))
    lockpath = tsv + ".lock.json"
    lock = None
    if os.path.isfile(lockpath):
        with open(lockpath, encoding="utf-8") as f:
            lock = json.load(f)
        log("Снапшот загружен: %d записей" % len(lock))
    else:
        log("[!] Снапшот не найден — проверка только по имени текстуры")
    with open(tsv, "r", encoding="utf-8-sig") as f:
        rows = [l.rstrip("\n").rstrip("\r") for l in f if l.strip()]
    if not rows:
        log("Таблица пуста")
        return
    head = rows[0].split("\t")
    idx = {c: i for i, c in enumerate(head)}
    for need in ("rec_off", "name", "w", "h"):
        if need not in idx:
            log("В таблице нет колонки '%s'" % need)
            return
    changed = 0
    fields_changed = 0
    errors = 0
    for ln, line in enumerate(rows[1:], 2):
        c = line.split("\t")

        def get(col):
            i = idx.get(col)
            return c[i].strip() if (i is not None and i < len(c)) else ""

        try:
            off = int(get("rec_off"), 16) if get("rec_off").lower().startswith("0x") \
                else int(get("rec_off"))
        except ValueError:
            log("  строка %d: плохой rec_off" % ln)
            errors += 1
            continue
        name = get("name")
        lid = get("layout") or "I24p20"
        if lid not in SP_LAY:
            log("  строка %d: неизвестный layout '%s'" % (ln, lid))
            errors += 1
            continue
        layout = SP_LAY[lid]
        size, ptroff, fields = layout[1], layout[2], layout[3]
        if off < 0 or off + size > len(data):
            log("  строка %d: rec_off вне файла" % ln)
            errors += 1
            continue
        # проверка указателя на имя
        pv = struct.unpack_from("<I", data, off + ptroff)[0]
        po = sp_va2off(pv, base, secs, len(data))
        real = None
        if po is not None:
            real = bytes(data[po:po + 48]).split(b"\x00")[0].decode("ascii", "replace")
        if real != name:
            log("  строка %d: имя не совпало (в exe '%s', в таблице '%s') — пропуск"
                % (ln, real, name))
            errors += 1
            continue
        key = "0x%08X" % off
        if lock and key in lock:
            snap = lock[key]
            cur = bytes(data[off:off + size]).hex()
            if snap.get("raw") != cur:
                log("  строка %d: запись уже изменена (не совпала со снапшотом) — пропуск" % ln)
                errors += 1
                continue
        before = bytes(data[off:off + size])
        diffs = []
        for foff, t, fname in fields:
            raw = get(fname)
            if raw == "":
                continue
            try:
                num = sp_num(raw)
            except ValueError:
                log("  строка %d: плохое число в '%s'" % (ln, fname))
                continue
            if num is None:
                continue
            if t == 'f':
                new = float(num)
                if not (-1e6 < new < 1e6):
                    continue
            else:
                new = int(round(num))
                if t == 'I' and new < 0:
                    log("  строка %d: '%s' не может быть отрицательным" % (ln, fname))
                    continue
            old = struct.unpack_from("<" + t, data, off + foff)[0]
            if old == new:
                continue
            struct.pack_into("<" + t, data, off + foff, new)
            diffs.append("%s %g->%g" % (fname, old, new))
        if bytes(data[off:off + size]) != before:
            changed += 1
            fields_changed += len(diffs)
            log("  %s %-26s %s" % (key, name, ", ".join(diffs)))
    log("")
    log("Записей изменено: %d, полей: %d, ошибок: %d" % (changed, fields_changed, errors))
    if dry:
        log("--dry: файл НЕ записан")
        return
    if changed == 0:
        log("Изменений нет — файл не создан")
        return
    with open(out_exe, "wb") as f:
        f.write(data)
    log("Сохранено: %s" % out_exe)
    log("Оригинал не изменён.")


class ExeSpriteTab:
    """GUI-обёртка ExeTextureEdit.py: экспорт TSV -> правка -> импорт."""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.exe_path = tk.StringVar()
        self.txd_dir = tk.StringVar()
        self.tsv_path = tk.StringVar()
        self.out_exe = tk.StringVar()
        self.all_names = tk.BooleanVar(value=False)
        self.min_score = tk.IntVar(value=2)
        self.dry = tk.BooleanVar(value=False)
        self._q = queue.Queue()
        self._busy = False
        self._btns = []
        self._ui()
        self.frame.after(100, self._pump)

    def _ui(self):
        top = ttk.LabelFrame(self.frame, text="Файлы", padding=8)
        top.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(top, text="EXE (оригинал):", width=22, anchor='w').grid(row=0, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.exe_path).grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(top, text="...", width=4, command=self.pick_exe).grid(row=0, column=2)
        ttk.Label(top, text="Папка TXD (необяз.):", width=22, anchor='w').grid(row=1, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.txd_dir).grid(row=1, column=1, sticky='ew', padx=4)
        ttk.Button(top, text="...", width=4, command=self.pick_txd).grid(row=1, column=2)
        ttk.Label(top, text="TSV таблица:", width=22, anchor='w').grid(row=2, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.tsv_path).grid(row=2, column=1, sticky='ew', padx=4)
        ttk.Button(top, text="...", width=4, command=self.pick_tsv).grid(row=2, column=2)
        ttk.Label(top, text="Новый EXE (импорт):", width=22, anchor='w').grid(row=3, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.out_exe).grid(row=3, column=1, sticky='ew', padx=4)
        ttk.Button(top, text="...", width=4, command=self.pick_out).grid(row=3, column=2)
        top.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(self.frame, text="Параметры", padding=8)
        opt.pack(fill=tk.X, padx=6, pady=(0, 6))
        ttk.Checkbutton(opt, text="--all (все имена, без фильтра по TXD)",
                        variable=self.all_names, command=self._on_all).pack(side=tk.LEFT)
        ttk.Label(opt, text="   мин. score:").pack(side=tk.LEFT)
        tk.Spinbox(opt, from_=1, to=9, textvariable=self.min_score, width=4).pack(side=tk.LEFT)
        ttk.Checkbutton(opt, text="   --dry (импорт без записи файла)",
                        variable=self.dry).pack(side=tk.LEFT, padx=10)

        btns = ttk.Frame(self.frame)
        btns.pack(fill=tk.X, padx=6, pady=(0, 6))
        b1 = ttk.Button(btns, text="ЭКСПОРТ СПРАЙТОВ В TSV", command=self.do_export)
        b1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        b2 = ttk.Button(btns, text="ИМПОРТ TSV В EXE", command=self.do_import)
        b2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self._btns = [b1, b2]

        lf = ttk.LabelFrame(self.frame, text="Лог", padding=4)
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.log = scrolledtext.ScrolledText(lf, wrap='none', height=14, font=('Consolas', 9))
        self.log.pack(fill=tk.BOTH, expand=True)
        bar = ttk.Frame(lf); bar.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(bar, text="Очистить", command=lambda: self.log.delete('1.0', tk.END)).pack(side=tk.LEFT)
        ttk.Button(bar, text="Сохранить лог…", command=self.save_log).pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text="Рядом с TSV создаётся .lock.json — не удаляй!",
                  foreground='gray').pack(side=tk.LEFT, padx=10)

    def _on_all(self):
        self.min_score.set(1 if self.all_names.get() else 2)

    def pick_exe(self):
        p = filedialog.askopenfilename(parent=self.frame, title="TMNT.EXE",
                                       filetypes=[("EXE", "*.exe"), ("All", "*.*")])
        if p:
            self.exe_path.set(p)
            base = os.path.splitext(p)[0]
            if not self.tsv_path.get():
                self.tsv_path.set(base + "_sprites.tsv")
            if not self.out_exe.get():
                self.out_exe.set(base + "_patched.exe")

    def pick_txd(self):
        p = filedialog.askdirectory(parent=self.frame, title="Папка с .txd (для сверки имён)")
        if p:
            self.txd_dir.set(p)

    def pick_tsv(self):
        p = filedialog.asksaveasfilename(parent=self.frame, title="TSV таблица",
                                         defaultextension=".tsv",
                                         filetypes=[("TSV", "*.tsv"), ("All", "*.*")],
                                         confirmoverwrite=False)
        if p:
            self.tsv_path.set(p)

    def pick_out(self):
        p = filedialog.asksaveasfilename(parent=self.frame, title="Новый EXE",
                                         defaultextension=".exe",
                                         filetypes=[("EXE", "*.exe"), ("All", "*.*")])
        if p:
            self.out_exe.set(p)

    def save_log(self):
        p = filedialog.asksaveasfilename(parent=self.frame, initialfile="exe_sprites_log.txt",
                                         defaultextension=".txt",
                                         filetypes=[("TXT", "*.txt")])
        if p:
            with open(p, 'w', encoding='utf-8') as f:
                f.write(self.log.get('1.0', 'end-1c'))

    def _log(self, m):
        self._q.put(('log', str(m)))

    def _pump(self):
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == 'log':
                    self.log.insert(tk.END, val + "\n")
                    self.log.see(tk.END)
                elif kind == 'done':
                    self._busy = False
                    for b in self._btns:
                        b.configure(state='normal')
        except queue.Empty:
            pass
        try:
            if self.frame.winfo_exists():
                self.frame.after(100, self._pump)
        except Exception:
            pass

    def _start(self, fn):
        if self._busy:
            messagebox.showwarning("Занято", "Дождись окончания операции", parent=self.frame)
            return
        self._busy = True
        for b in self._btns:
            b.configure(state='disabled')

        def worker():
            try:
                fn()
            except Exception as e:
                self._q.put(('log', "[!!] %s" % e))
            finally:
                self._q.put(('done', None))

        threading.Thread(target=worker, daemon=True).start()

    def do_export(self):
        exe = self.exe_path.get().strip()
        tsv = self.tsv_path.get().strip()
        txd = self.txd_dir.get().strip()
        if not exe or not os.path.isfile(exe):
            messagebox.showerror("Ошибка", "Укажи EXE файл", parent=self.frame); return
        if not tsv:
            tsv = os.path.splitext(exe)[0] + "_sprites.tsv"
            self.tsv_path.set(tsv)
        if txd and not os.path.isdir(txd):
            messagebox.showerror("Ошибка", "Папка TXD не найдена", parent=self.frame); return
        try:
            ms = int(self.min_score.get())
        except Exception:
            ms = 2
        alln = self.all_names.get()
        self.log.delete('1.0', tk.END)
        self._log("=== ЭКСПОРТ спрайтов ===")
        self._log("EXE: %s" % exe)
        if txd:
            self._log("TXD: %s" % txd)
        self._log("min-score=%d, --all=%s" % (ms, alln))

        def job():
            sp_export(exe, txd or None, tsv, ms, alln, self._log)

        self._start(job)

    def do_import(self):
        exe = self.exe_path.get().strip()
        tsv = self.tsv_path.get().strip()
        out = self.out_exe.get().strip()
        dry = self.dry.get()
        if not exe or not os.path.isfile(exe):
            messagebox.showerror("Ошибка", "Укажи EXE файл", parent=self.frame); return
        if not tsv or not os.path.isfile(tsv):
            messagebox.showerror("Ошибка", "Укажи TSV таблицу", parent=self.frame); return
        if not out:
            out = os.path.splitext(exe)[0] + "_patched.exe"
            self.out_exe.set(out)
        if not dry and os.path.abspath(out) == os.path.abspath(exe):
            messagebox.showerror("Ошибка", "Нельзя писать поверх оригинала — выбери другой файл",
                                 parent=self.frame); return
        self.log.delete('1.0', tk.END)
        self._log("=== ИМПОРТ спрайтов ===")
        self._log("EXE: %s" % exe)
        self._log("TSV: %s" % tsv)
        self._log("OUT: %s%s" % (out, "   (--dry)" if dry else ""))

        def job():
            sp_import(exe, tsv, out, dry, self._log)

        self._start(job)


# ===================== ОКНО ЛОГА =====================
class LogWindow(tk.Toplevel):
    def __init__(self, master, title="Лог", initial=""):
        super().__init__(master)
        self.title(title)
        self.geometry("1000x650")
        self.autoscroll = tk.BooleanVar(value=True)
        self.topmost = tk.BooleanVar(value=False)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar, text="Копировать всё", command=self.copy_all).pack(side="left")
        ttk.Button(bar, text="Копировать выделенное", command=self.copy_sel).pack(side="left", padx=6)
        ttk.Button(bar, text="Сохранить…", command=self.save).pack(side="left")
        ttk.Button(bar, text="Очистить окно", command=self.clear).pack(side="left", padx=6)
        ttk.Checkbutton(bar, text="Автоскролл", variable=self.autoscroll).pack(side="left", padx=10)
        ttk.Checkbutton(bar, text="Поверх всех", variable=self.topmost,
                        command=self._apply_top).pack(side="left")
        self.text = scrolledtext.ScrolledText(self, wrap="none", font=("Consolas", 9),
                                              state="disabled")
        self.text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        menu = tk.Menu(self.text, tearoff=0)
        menu.add_command(label="Копировать", command=self.copy_sel)
        menu.add_command(label="Копировать всё", command=self.copy_all)
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=self.select_all)
        menu.add_command(label="Сохранить в файл…", command=self.save)
        menu.add_command(label="Очистить окно", command=self.clear)
        self.text.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.text.bind("<Control-c>", lambda e: self.copy_sel())
        self.text.bind("<Control-C>", lambda e: self.copy_sel())
        self.text.bind("<Control-a>", lambda e: self.select_all())
        self.text.bind("<Control-A>", lambda e: self.select_all())
        self.text.bind("<Control-s>", lambda e: self.save())
        self.text.bind("<Control-S>", lambda e: self.save())
        if initial:
            self.append(initial, raw=True)

    def _apply_top(self):
        self.attributes("-topmost", bool(self.topmost.get()))

    def append(self, msg, raw=False):
        if not self.winfo_exists():
            return
        self.text.configure(state="normal")
        self.text.insert("end", msg if raw else msg + "\n")
        if self.autoscroll.get():
            self.text.see("end")
        self.text.configure(state="disabled")

    def get_all(self):
        return self.text.get("1.0", "end-1c")

    def copy_all(self):
        data = self.get_all()
        if data:
            self.clipboard_clear()
            self.clipboard_append(data)

    def copy_sel(self):
        try:
            data = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            data = ""
        if data:
            self.clipboard_clear()
            self.clipboard_append(data)

    def select_all(self):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def save(self):
        p = filedialog.asksaveasfilename(parent=self, title="Сохранить лог",
                                         initialfile="tool_log.txt", defaultextension=".txt",
                                         filetypes=[("TXT", "*.txt"), ("All", "*.*")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.get_all())


# ------------------------- GUI -------------------------
class UltimateGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TMNT 2003 Ultimate Modding Tool")
        self.root.geometry("1300x950")
        self.root.minsize(1000, 700)
        self.aex_folder = tk.StringVar()
        self.aex_files_folder = tk.StringVar()
        self.aex_out_folder = tk.StringVar()
        self.aex_audio_folder = tk.StringVar()
        self.aex_import_audio = tk.BooleanVar(value=False)
        self.aex_normalize_audio = tk.BooleanVar(value=True)
        self.afs_folder = tk.StringVar()
        self.afs_files_folder = tk.StringVar()
        self.afs_unpack_folder = tk.StringVar()
        self.txd_folder = tk.StringVar()
        self.pac_folder = tk.StringVar()
        self.unpack_folder = tk.StringVar()
        self.adx_in_folder = tk.StringVar()
        self.patched_folder = tk.StringVar()
        self.png_in_folder = tk.StringVar()
        self.txd_out_folder = tk.StringVar()
        self.txd_pac_in_folder = tk.StringVar()
        self.audio_src_folder = tk.StringVar()
        self.audio_dst_folder = tk.StringVar()
        self.audio_normalize = tk.BooleanVar(value=True)
        self.txd_allow_resize = tk.BooleanVar(value=False)
        self.txd_size_mode = tk.StringVar(value="fit")
        self.txd_dup_to_base = tk.BooleanVar(value=True)
        self._log_q = queue.Queue()
        self._ui_q = queue.Queue()
        self.log_buffer = []
        self.log_windows = []
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_aex_tab()
        self._build_afs_tab()
        self._build_txd_tab()
        self._build_pac_tab()
        self._build_audio_tab()
        self._build_text_tab()
        self._build_exe_tab()
        log_frame = ttk.LabelFrame(root, text="Лог (Ctrl+C — копировать, правый клик — меню)", padding=5)
        log_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log_widget = scrolledtext.ScrolledText(log_frame, wrap="none", height=10,
                                                    font=("Consolas", 9), state="disabled")
        self.log_widget.pack(fill="both", expand=True)
        self._bind_log_widget(self.log_widget)
        bar = ttk.Frame(log_frame)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="Копировать весь лог", command=self.copy_log).pack(side="left")
        ttk.Button(bar, text="Сохранить лог…", command=self.save_log).pack(side="left", padx=6)
        ttk.Button(bar, text="Отдельное окно", command=self.open_log_window).pack(side="left")
        ttk.Button(bar, text="Очистить", command=self.clear_log).pack(side="right")
        self.root.after(80, self._pump)

    # ---------- лог ----------
    def _bind_log_widget(self, w):
        menu = tk.Menu(w, tearoff=0)
        menu.add_command(label="Копировать", command=lambda: self._copy_sel(w))
        menu.add_command(label="Копировать весь лог", command=self.copy_log)
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_all(w))
        menu.add_command(label="Сохранить лог в файл…", command=self.save_log)
        menu.add_command(label="Открыть в отдельном окне", command=self.open_log_window)
        menu.add_command(label="Очистить лог", command=self.clear_log)
        w.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        w.bind("<Control-c>", lambda e: self._copy_sel(w))
        w.bind("<Control-C>", lambda e: self._copy_sel(w))
        w.bind("<Control-a>", lambda e: self._select_all(w))
        w.bind("<Control-A>", lambda e: self._select_all(w))
        w.bind("<Control-s>", lambda e: self.save_log())
        w.bind("<Control-S>", lambda e: self.save_log())

    def _copy_sel(self, w):
        try:
            data = w.get("sel.first", "sel.last")
        except tk.TclError:
            data = ""
        if data:
            self.root.clipboard_clear()
            self.root.clipboard_append(data)

    def _select_all(self, w):
        w.tag_add("sel", "1.0", "end-1c")
        return "break"

    def log(self, msg):
        self._log_q.put(str(msg))

    def ui_info(self, title, text):
        self._ui_q.put(lambda: messagebox.showinfo(title, text))

    def ui_error(self, title, text):
        self._ui_q.put(lambda: messagebox.showerror(title, text))

    def _pump(self):
        try:
            while True:
                self._append_log(self._log_q.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self.root.after(80, self._pump)
        except Exception:
            pass

    def _append_log(self, msg):
        self.log_buffer.append(msg)
        w = self.log_widget
        w.configure(state="normal")
        w.insert("end", msg + "\n")
        w.see("end")
        w.configure(state="disabled")
        alive = []
        for win in self.log_windows:
            try:
                if win.winfo_exists():
                    win.append(msg)
                    alive.append(win)
            except Exception:
                pass
        self.log_windows = alive

    def copy_log(self):
        data = "\n".join(self.log_buffer)
        if data:
            self.root.clipboard_clear()
            self.root.clipboard_append(data)

    def save_log(self):
        p = filedialog.asksaveasfilename(title="Сохранить лог", initialfile="tool_log.txt",
                                         defaultextension=".txt",
                                         filetypes=[("TXT", "*.txt"), ("All", "*.*")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(self.log_buffer))

    def open_log_window(self):
        win = LogWindow(self.root, "Лог — TMNT Tool", "\n".join(self.log_buffer))
        self.log_windows.append(win)

    def clear_log(self):
        self.log_buffer = []
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    # ---------- вкладки ----------
    def _build_aex_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="AEX (ADX)")
        top = ttk.LabelFrame(f, text="Извлечение ADX из AEX (рекурсивно)", padding=8)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Label(top, text="Папка с .aex:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.aex_folder, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.aex_folder, "Папка с .aex")).grid(row=0, column=2)
        ttk.Label(top, text="Папка вывода:").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.unpack_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.unpack_folder, "Папка вывода")).grid(row=1, column=2)
        ttk.Button(top, text="Извлечь ADX", command=self.do_aex_unpack).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        mid = ttk.LabelFrame(f, text="Пересборка AEX (любая длина файлов)", padding=8)
        mid.pack(fill="x", padx=5, pady=5)
        ttk.Label(mid, text="Папка с .aex:").grid(row=0, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.aex_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(mid, text="Папка с файлами (где _order.txt):").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.aex_files_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.aex_files_folder, "Папка с файлами")).grid(row=1, column=2)
        ttk.Label(mid, text="Папка для новых AEX:").grid(row=2, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.aex_out_folder, width=60).grid(row=2, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.aex_out_folder, "Папка для новых AEX")).grid(row=2, column=2)
        ttk.Checkbutton(mid, text="Импортировать WAV/FLAC/MP3 как ADX (через ffmpeg)",
                        variable=self.aex_import_audio).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Checkbutton(mid, text="Приводить аудио к формату игры: 24000 Hz / 16 bit / mono",
                        variable=self.aex_normalize_audio).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(mid, text="Папка с аудио для импорта:").grid(row=5, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.aex_audio_folder, width=60).grid(row=5, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.aex_audio_folder, "Папка с аудио")).grid(row=5, column=2)
        ttk.Button(mid, text="Пересобрать AEX", command=self.do_aex_rebuild).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(5, 0))

    def _build_afs_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="AFS / DAT")
        top = ttk.LabelFrame(f, text="Извлечение AFS/DAT (рекурсивно)", padding=8)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Label(top, text="Папка с .afs/.dat:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.afs_folder, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.afs_folder, "Папка с AFS/DAT")).grid(row=0, column=2)
        ttk.Label(top, text="Папка вывода:").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.unpack_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.unpack_folder, "Папка вывода")).grid(row=1, column=2)
        ttk.Button(top, text="Извлечь AFS/DAT", command=self.do_afs_unpack).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        mid = ttk.LabelFrame(f, text="Замена в AFS/DAT (на месте, размеры должны совпадать)", padding=8)
        mid.pack(fill="x", padx=5, pady=5)
        ttk.Label(mid, text="Папка с .afs/.dat:").grid(row=0, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.afs_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(mid, text="Папка с новыми файлами:").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.afs_files_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.afs_files_folder, "Папка с файлами")).grid(row=1, column=2)
        ttk.Label(mid, text="Папка для патченных:").grid(row=2, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.patched_folder, width=60).grid(row=2, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.patched_folder, "Папка для патченных")).grid(row=2, column=2)
        ttk.Button(mid, text="Запатчить AFS/DAT (замена на месте)", command=self.do_afs_patch).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        bot = ttk.LabelFrame(f, text="Пересборка AFS/DAT", padding=8)
        bot.pack(fill="x", padx=5, pady=5)
        ttk.Label(bot, text="Папка с .afs/.dat:").grid(row=0, column=0, sticky="w")
        ttk.Entry(bot, textvariable=self.afs_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(bot, text="Папка с распакованной структурой:").grid(row=1, column=0, sticky="w")
        ttk.Entry(bot, textvariable=self.afs_unpack_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(bot, text="...", command=lambda: self._browse(self.afs_unpack_folder, "Папка с распакованными файлами")).grid(row=1, column=2)
        ttk.Label(bot, text="Папка для новых архивов:").grid(row=2, column=0, sticky="w")
        ttk.Entry(bot, textvariable=self.patched_folder, width=60).grid(row=2, column=1, padx=5)
        ttk.Button(bot, text="...", command=lambda: self._browse(self.patched_folder, "Папка для новых архивов")).grid(row=2, column=2)
        ttk.Button(bot, text="Пересобрать AFS/DAT (AFSbuild.exe)", command=self.do_afs_rebuild).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        ttk.Button(bot, text="Пересобрать AFS/DAT (встроенный: дубликаты имён + любые размеры)",
                   command=self.do_afs_rebuild_native).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(5, 0))

    def _build_txd_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="TXD (PNG)")
        top = ttk.LabelFrame(f, text="Извлечение PNG из TXD (рекурсивно, с альфа-каналом)", padding=8)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Label(top, text="Папка с .txd:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.txd_folder, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.txd_folder, "Папка с TXD")).grid(row=0, column=2)
        ttk.Label(top, text="Папка вывода (PNG):").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.unpack_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.unpack_folder, "Папка вывода")).grid(row=1, column=2)
        ttk.Button(top, text="Извлечь PNG", command=self.do_txd_unpack).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        mid = ttk.LabelFrame(f, text="Упаковка PNG обратно в TXD", padding=8)
        mid.pack(fill="x", padx=5, pady=5)
        ttk.Label(mid, text="Папка с .txd (шаблоны):").grid(row=0, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.txd_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(mid, text="Папка с PNG (рекурсивно):").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.png_in_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.png_in_folder, "Папка с PNG")).grid(row=1, column=2)
        ttk.Label(mid, text="Папка для новых TXD:").grid(row=2, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.txd_out_folder, width=60).grid(row=2, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.txd_out_folder, "Папка для новых TXD")).grid(row=2, column=2)
        sz = ttk.LabelFrame(mid, text="Если размер PNG не совпадает с оригиналом", padding=6)
        sz.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(sz, text="Дубликаты TXD (dojo__1 / dojo_2) > брать PNG из папки 'dojo' "
                                 "и вписывать под оригинал",
                        variable=self.txd_dup_to_base).pack(anchor="w", pady=(0, 4))
        ttk.Radiobutton(sz, text="Вписывать PNG в оригинальный размер — БЕЗОПАСНО",
                        variable=self.txd_size_mode, value="fit").pack(anchor="w")
        ttk.Radiobutton(sz, text="Пропускать такие текстуры (оставлять оригинал)",
                        variable=self.txd_size_mode, value="skip").pack(anchor="w")
        ttk.Radiobutton(sz, text="Менять размер в TXD — ЭКСПЕРИМЕНТАЛЬНО",
                        variable=self.txd_size_mode, value="resize").pack(anchor="w")
        btns = ttk.Frame(mid)
        btns.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="Упаковать PNG в TXD", command=self.do_txd_pack).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Проверить размеры PNG", command=self.do_txd_check).pack(side="left", padx=(8, 0))

    def _build_pac_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="PAC (TXD)")
        top = ttk.LabelFrame(f, text="Извлечение TXD из PAC (рекурсивно)", padding=8)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Label(top, text="Папка с .pac:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.pac_folder, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.pac_folder, "Папка с PAC")).grid(row=0, column=2)
        ttk.Label(top, text="Папка вывода:").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.unpack_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.unpack_folder, "Папка вывода")).grid(row=1, column=2)
        ttk.Button(top, text="Извлечь TXD из PAC", command=self.do_pac_unpack).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        mid = ttk.LabelFrame(f, text="Замена TXD в PAC (только при совпадении размера)", padding=8)
        mid.pack(fill="x", padx=5, pady=5)
        ttk.Label(mid, text="Папка с .pac:").grid(row=0, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.pac_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(mid, text="Папка с новыми TXD:").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.txd_pac_in_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.txd_pac_in_folder, "Папка с TXD")).grid(row=1, column=2)
        ttk.Label(mid, text="Папка для патченных PAC:").grid(row=2, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.patched_folder, width=60).grid(row=2, column=1, padx=5)
        ttk.Button(mid, text="...", command=lambda: self._browse(self.patched_folder, "Папка для патченных")).grid(row=2, column=2)
        ttk.Button(mid, text="Запатчить PAC", command=self.do_pac_patch).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 0))

    def _build_audio_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="Аудио (ffmpeg)")
        info = ttk.Label(f, text=f"ffmpeg.exe ожидается рядом со скриптом:\n{FFMPEG}", foreground="gray")
        info.pack(fill="x", padx=5, pady=(5, 0))
        top = ttk.LabelFrame(f, text="ADX > FLAC / WAV", padding=8)
        top.pack(fill="x", padx=5, pady=5)
        ttk.Label(top, text="Папка с ADX:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.audio_src_folder, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.audio_src_folder, "Папка с ADX")).grid(row=0, column=2)
        ttk.Label(top, text="Папка для результата:").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.audio_dst_folder, width=60).grid(row=1, column=1, padx=5)
        ttk.Button(top, text="...", command=lambda: self._browse(self.audio_dst_folder, "Папка результата")).grid(row=1, column=2)
        btns = ttk.Frame(top)
        btns.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        ttk.Button(btns, text="ADX > FLAC", command=self.do_adx_to_flac).pack(side="left", padx=(0, 10))
        ttk.Button(btns, text="ADX > WAV", command=self.do_adx_to_wav).pack(side="left")

        mid = ttk.LabelFrame(f, text="WAV / MP3 / FLAC > ADX", padding=8)
        mid.pack(fill="x", padx=5, pady=5)
        ttk.Label(mid, text="Папка с аудио:").grid(row=0, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.audio_src_folder, width=60, state="readonly").grid(row=0, column=1, padx=5)
        ttk.Label(mid, text="Папка для ADX:").grid(row=1, column=0, sticky="w")
        ttk.Entry(mid, textvariable=self.audio_dst_folder, width=60, state="readonly").grid(row=1, column=1, padx=5)
        ttk.Checkbutton(mid, text="Приводить к формату игры: 24000 Hz / 16 bit / mono",
                        variable=self.audio_normalize).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Button(mid, text="В ADX", command=self.do_to_adx).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 0))

    def _build_text_tab(self):
        """ПОЛНОСТЬЮ DatEditText.py: Редактор + Патч-реплейсер."""
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="Текст (DAT)")
        nb = ttk.Notebook(f)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.dat_editor = DatEditorTab(nb)
        self.dat_patch = DatPatchTab(nb)
        nb.add(self.dat_editor.frame, text="Редактор")
        nb.add(self.dat_patch.frame, text="Патч-реплейсер")

    def _build_exe_tab(self):
        """ExeEditText.py + ExeTextureEdit.py."""
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="EXE")
        nb = ttk.Notebook(f)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.exe_text = ExeTextTab(nb)
        self.exe_sprites = ExeSpriteTab(nb)
        nb.add(self.exe_text.frame, text="Текст EXE (фикс. слоты)")
        nb.add(self.exe_sprites.frame, text="Спрайты EXE (TSV)")

    def _browse(self, var, title):
        d = filedialog.askdirectory(title=title)
        if d:
            var.set(d)

    # ---------- AEX ----------
    def do_aex_unpack(self):
        aex = self.aex_folder.get()
        out = self.unpack_folder.get()
        if not aex or not os.path.isdir(aex):
            messagebox.showerror("Ошибка", "Укажите папку с .aex"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку вывода"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== AEX: извлечение ===")

        def worker():
            try:
                n, m = AexTool.unpack_all(aex, out, self.log)
                self.log(f"\nИзвлечено {n} ADX из {m} архивов.")
                self.ui_info("Готово", f"Извлечено {n} ADX")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_aex_rebuild(self):
        aex = self.aex_folder.get()
        files = self.aex_files_folder.get()
        out = self.aex_out_folder.get()
        import_audio = self.aex_import_audio.get()
        normalize_audio = self.aex_normalize_audio.get()
        audio_dir = self.aex_audio_folder.get()
        if not aex or not os.path.isdir(aex):
            messagebox.showerror("Ошибка", "Укажите папку с .aex"); return
        if not files or not os.path.isdir(files):
            messagebox.showerror("Ошибка", "Укажите папку с файлами"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку для новых AEX"); return
        if import_audio and (not audio_dir or not os.path.isdir(audio_dir)):
            messagebox.showerror("Ошибка", "Укажите папку с аудио для импорта"); return
        if import_audio and not os.path.exists(FFMPEG):
            messagebox.showerror("Ошибка", f"ffmpeg.exe не найден:\n{FFMPEG}"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== AEX: пересборка ===")

        def worker():
            try:
                n, m = AexTool.patch_all(aex, files, out, self.log,
                                         import_audio=import_audio, audio_folder=audio_dir,
                                         normalize_audio=normalize_audio)
                self.log(f"\nПересобрано {m} архивов, импортировано/заменено {n} файлов.")
                self.ui_info("Готово", f"Архивов: {m}\nФайлов: {n}")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- AFS ----------
    def do_afs_unpack(self):
        afs = self.afs_folder.get()
        out = self.unpack_folder.get()
        if not afs or not os.path.isdir(afs):
            messagebox.showerror("Ошибка", "Укажите папку с AFS/DAT"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку вывода"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== AFS/DAT: извлечение ===")

        def worker():
            try:
                n, m = AfsTool.unpack_all(afs, out, self.log)
                self.log(f"\nИзвлечено {n} файлов из {m} архивов.")
                self.ui_info("Готово", f"Извлечено {n}")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_afs_patch(self):
        afs = self.afs_folder.get()
        files = self.afs_files_folder.get()
        patched = self.patched_folder.get()
        if not afs or not os.path.isdir(afs):
            messagebox.showerror("Ошибка", "Укажите папку с AFS/DAT"); return
        if not files or not os.path.isdir(files):
            messagebox.showerror("Ошибка", "Укажите папку с новыми файлами"); return
        if not patched:
            messagebox.showerror("Ошибка", "Укажите папку для патченных"); return
        self.clear_log(); self.log("=== AFS/DAT: патч ===")

        def worker():
            try:
                n, mis = AfsTool.replace_all(afs, files, patched, self.log)
                self.log(f"\nЗаменено {n}, пропущено по размеру {mis}")
                self.ui_info("Готово", f"Заменено {n}\nПропущено: {mis}")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_afs_rebuild(self):
        afs = self.afs_folder.get()
        unpack = self.afs_unpack_folder.get()
        out = self.patched_folder.get()
        if not afs or not os.path.isdir(afs):
            messagebox.showerror("Ошибка", "Укажите папку с AFS/DAT"); return
        if not unpack or not os.path.isdir(unpack):
            messagebox.showerror("Ошибка", "Укажите папку с распакованными файлами"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку для новых архивов"); return
        if not os.path.exists(AFSBUILD):
            messagebox.showerror("Ошибка", f"AFSbuild.exe не найден:\n{AFSBUILD}"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== AFS/DAT: пересборка через AFSbuild.exe ===")

        def worker():
            try:
                n = AfsTool.rebuild_with_afsbuild(afs, unpack, out, self.log)
                self.log(f"\nПересобрано {n} архивов.")
                self.ui_info("Готово", f"Пересобрано {n} архивов")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_afs_rebuild_native(self):
        afs = self.afs_folder.get()
        unpack = self.afs_unpack_folder.get()
        out = self.patched_folder.get()
        if not afs or not os.path.isdir(afs):
            messagebox.showerror("Ошибка", "Укажите папку с AFS/DAT"); return
        if not unpack or not os.path.isdir(unpack):
            messagebox.showerror("Ошибка", "Укажите папку с распакованными файлами"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку для новых архивов"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== AFS/DAT: встроенная пересборка ===")

        def worker():
            try:
                n = AfsTool.rebuild_native(afs, unpack, out, self.log)
                self.log("\nПересобрано %d архивов." % n)
                self.ui_info("Готово", "Пересобрано %d архивов" % n)
            except Exception as e:
                self.log("[!] %s" % e); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- TXD ----------
    def do_txd_unpack(self):
        txd = self.txd_folder.get()
        out = self.unpack_folder.get()
        if not txd or not os.path.isdir(txd):
            messagebox.showerror("Ошибка", "Укажите папку с TXD"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку вывода"); return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== TXD: извлечение PNG ===")

        def worker():
            try:
                files = find_files_recursive(txd, ('.txd',))
                total = 0
                for path in files:
                    rel = os.path.relpath(path, txd)
                    rel_dir = os.path.dirname(rel)
                    self.log(f"--- {rel} ---")
                    out_dir = os.path.join(out, rel_dir)
                    os.makedirs(out_dir, exist_ok=True)
                    total += txd_unpack_file(path, out_dir, self.log)
                self.log(f"\nИзвлечено {total} изображений.")
                self.ui_info("Готово", f"Извлечено {total} PNG")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_txd_check(self):
        txd = self.txd_folder.get()
        png = self.png_in_folder.get()
        dup_to_base = self.txd_dup_to_base.get()
        if not txd or not os.path.isdir(txd):
            messagebox.showerror("Ошибка", "Укажите папку с TXD"); return
        if not png or not os.path.isdir(png):
            messagebox.showerror("Ошибка", "Укажите папку с PNG"); return
        self.clear_log(); self.log("=== TXD: проверка размеров PNG ===")

        def worker():
            try:
                png_index = build_png_index(png)
                self.log(f"Найдено PNG: {len(png_index)}")
                total_diff = 0
                total_tex = 0
                for path in sorted(find_files_recursive(txd, ('.txd',))):
                    rel = os.path.relpath(path, txd)
                    self.log(f"--- {rel} ---")
                    d, n = txd_report_sizes(path, png_index, self.log, dup_to_base=dup_to_base)
                    total_diff += d
                    total_tex += n
                self.log(f"\nТекстур: {total_tex}, с другим размером: {total_diff}")
                self.ui_info("Проверка", f"Текстур: {total_tex}\nС другим размером: {total_diff}")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_txd_pack(self):
        txd = self.txd_folder.get()
        png = self.png_in_folder.get()
        out = self.txd_out_folder.get()
        mode = self.txd_size_mode.get()
        allow_resize = (mode == "resize")
        fit_size = (mode == "fit")
        dup_to_base = self.txd_dup_to_base.get()
        self.txd_allow_resize.set(allow_resize)
        if not txd or not os.path.isdir(txd):
            messagebox.showerror("Ошибка", "Укажите папку с TXD"); return
        if not png or not os.path.isdir(png):
            messagebox.showerror("Ошибка", "Укажите папку с PNG"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку для новых TXD"); return
        if allow_resize:
            if not messagebox.askyesno(
                    "Экспериментальный режим",
                    "Менять размеры текстур в TXD — рискованно.\nВсё равно продолжить?"):
                return
        os.makedirs(out, exist_ok=True)
        self.clear_log(); self.log("=== TXD: упаковка PNG ===")

        def worker():
            try:
                png_index = build_png_index(png)
                self.log(f"Найдено PNG (рекурсивно): {len(png_index)}")
                if allow_resize:
                    self.log("Режим: МЕНЯТЬ РАЗМЕР В TXD")
                elif fit_size:
                    self.log("Режим: вписывать PNG в оригинальный размер")
                else:
                    self.log("Режим: пропускать несовпадающие размеры")
                files = find_files_recursive(txd, ('.txd',))
                done = 0
                for path in files:
                    rel = os.path.relpath(path, txd)
                    rel_dir = os.path.dirname(rel)
                    self.log(f"--- {rel} ---")
                    out_dir = os.path.join(out, rel_dir)
                    os.makedirs(out_dir, exist_ok=True)
                    ss, sk = txd_pack_file(path, png_index, out_dir, self.log,
                                           allow_resize=allow_resize, fit_size=fit_size,
                                           dup_to_base=dup_to_base)
                    if ss > 0 and sk < ss:
                        done += 1
                self.log(f"\nУпаковано {done} TXD.")
                self.ui_info("Готово", f"Упаковано {done} TXD")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- PAC ----------
    def do_pac_unpack(self):
        pac = self.pac_folder.get()
        out = self.unpack_folder.get()
        if not pac or not os.path.isdir(pac):
            messagebox.showerror("Ошибка", "Укажите папку с PAC"); return
        if not out:
            messagebox.showerror("Ошибка", "Укажите папку вывода"); return
        self.clear_log(); self.log("=== PAC: извлечение TXD ===")

        def worker():
            try:
                n = PacTool.extract_all(pac, out, self.log)
                self.log(f"\nИзвлечено {n} TXD.")
                self.ui_info("Готово", f"Извлечено {n} TXD")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_pac_patch(self):
        pac = self.pac_folder.get()
        txd_in = self.txd_pac_in_folder.get()
        patched = self.patched_folder.get()
        if not pac or not os.path.isdir(pac):
            messagebox.showerror("Ошибка", "Укажите папку с PAC"); return
        if not txd_in or not os.path.isdir(txd_in):
            messagebox.showerror("Ошибка", "Укажите папку с TXD"); return
        if not patched:
            messagebox.showerror("Ошибка", "Укажите папку для патченных"); return
        os.makedirs(patched, exist_ok=True)
        self.clear_log(); self.log("=== PAC: замена TXD ===")

        def worker():
            try:
                total = 0
                files = find_files_recursive(pac, ('.pac',))
                for path in files:
                    rel = os.path.relpath(path, pac)
                    rel_dir = os.path.dirname(rel)
                    self.log(f"--- {rel} ---")
                    out_path = os.path.join(patched, rel)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    txd_sub = os.path.join(txd_in, rel_dir)
                    total += PacTool.replace_txd(path, txd_sub, out_path, self.log)
                self.log(f"\nЗаменено {total} TXD.")
                self.ui_info("Готово", f"Заменено {total} TXD")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- AUDIO ----------
    def _check_audio_folders(self):
        src = self.audio_src_folder.get()
        dst = self.audio_dst_folder.get()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Укажите существующую папку с исходными файлами")
            return None, None
        if not dst:
            messagebox.showerror("Ошибка", "Укажите папку для результата")
            return None, None
        if not os.path.exists(FFMPEG):
            messagebox.showerror("Ошибка", f"ffmpeg.exe не найден:\n{FFMPEG}")
            return None, None
        os.makedirs(dst, exist_ok=True)
        return src, dst

    def do_adx_to_flac(self):
        src, dst = self._check_audio_folders()
        if src is None:
            return
        self.clear_log(); self.log("=== ADX > FLAC ===")

        def worker():
            try:
                n = AudioTool.convert_folder(src, dst, ".flac", self.log)
                self.log(f"\nСконвертировано {n} файлов.")
                self.ui_info("Готово", f"Сконвертировано {n} ADX > FLAC")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_adx_to_wav(self):
        src, dst = self._check_audio_folders()
        if src is None:
            return
        self.clear_log(); self.log("=== ADX > WAV ===")

        def worker():
            try:
                n = AudioTool.convert_folder(src, dst, ".wav", self.log)
                self.log(f"\nСконвертировано {n} файлов.")
                self.ui_info("Готово", f"Сконвертировано {n} ADX > WAV")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def do_to_adx(self):
        src, dst = self._check_audio_folders()
        if src is None:
            return
        normalize_audio = self.audio_normalize.get()
        self.clear_log(); self.log("=== АУДИО > ADX ===")
        if normalize_audio:
            self.log("Формат: 24000 Hz / 16 bit / mono")

        def worker():
            try:
                n = AudioTool.convert_to_adx(src, dst, self.log, normalize_audio=normalize_audio)
                self.log(f"\nСконвертировано {n} файлов в ADX.")
                self.ui_info("Готово", f"Сконвертировано {n} файлов > ADX")
            except Exception as e:
                self.log(f"[!] {e}"); self.ui_error("Ошибка", str(e))

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app = UltimateGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()