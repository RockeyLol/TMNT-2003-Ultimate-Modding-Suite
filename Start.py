#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ToolPlus.py — расширение TMNT Tool. Сам Tool.py НЕ изменяется.

Запуск:  python ToolPlus.py     (Tool.py должен лежать рядом)

Что добавляет:
  * EXE > Спрайты — редактор по имени текстуры, БЕЗ TSV (старая TSV-вкладка убирается)
  * SFD (видео)   — подмена аудио + пакетная обработка + извлечение аудио
  * Язык RU / EN  — переключатель сверху, перевод всего интерфейса
"""
import os
import re
import sys
import json
import struct
import queue
import shutil
import threading
import subprocess
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    import Tool
except ImportError:
    try:
        import tool as Tool
    except ImportError:
        print("Tool.py не найден рядом с ToolPlus.py")
        raise

CFG_PATH = os.path.join(SCRIPT_DIR, "toolplus.json")


# ============================================================================
# ============================  ЯЗЫК  ========================================
# ============================================================================
LANG = "ru"
ORIG = {}

TR = {
    # --- вкладки ---
    "AEX (ADX)": "AEX (ADX)", "AFS / DAT": "AFS / DAT", "TXD (PNG)": "TXD (PNG)",
    "PAC (TXD)": "PAC (TXD)", "Аудио (ffmpeg)": "Audio (ffmpeg)",
    "Текст (DAT)": "Text (DAT)", "Редактор": "Editor", "Патч-реплейсер": "Batch replacer",
    "Текст EXE (фикс. слоты)": "EXE text (fixed slots)",
    "Текст EXE (додзё и др.)": "EXE text (dojo etc.)",
    "Спрайты EXE (TSV)": "EXE sprites (TSV)",
    "Спрайты EXE (по имени)": "EXE sprites (by name)",
    "EXE (спрайты)": "EXE (sprites)", "SFD (видео)": "SFD (video)",
    # --- AEX ---
    "Извлечение ADX из AEX (рекурсивно)": "Extract ADX from AEX (recursive)",
    "Папка с .aex:": "Folder with .aex:", "Папка вывода:": "Output folder:",
    "Извлечь ADX": "Extract ADX",
    "Пересборка AEX (любая длина файлов)": "Rebuild AEX (any file length)",
    "Папка с файлами (где _order.txt):": "Folder with files (_order.txt):",
    "Папка для новых AEX:": "Folder for new AEX:",
    "Импортировать WAV/FLAC/MP3 как ADX (через ffmpeg)":
        "Import WAV/FLAC/MP3 as ADX (via ffmpeg)",
    "Приводить аудио к формату игры: 24000 Hz / 16 bit / mono":
        "Convert audio to game format: 24000 Hz / 16 bit / mono",
    "Папка с аудио для импорта:": "Audio folder for import:",
    "Пересобрать AEX": "Rebuild AEX",
    # --- AFS ---
    "Извлечение AFS/DAT (рекурсивно)": "Extract AFS/DAT (recursive)",
    "Папка с .afs/.dat:": "Folder with .afs/.dat:", "Извлечь AFS/DAT": "Extract AFS/DAT",
    "Замена в AFS/DAT (на месте, размеры должны совпадать)":
        "Replace inside AFS/DAT (in place, sizes must match)",
    "Папка с новыми файлами:": "Folder with new files:",
    "Папка для патченных:": "Folder for patched files:",
    "Запатчить AFS/DAT (замена на месте)": "Patch AFS/DAT (in-place replace)",
    "Пересборка AFS/DAT": "Rebuild AFS/DAT",
    "Папка с распакованной структурой:": "Folder with unpacked structure:",
    "Папка для новых архивов:": "Folder for new archives:",
    "Пересобрать AFS/DAT (AFSbuild.exe)": "Rebuild AFS/DAT (AFSbuild.exe)",
    "Пересобрать AFS/DAT (встроенный: дубликаты имён + любые размеры)":
        "Rebuild AFS/DAT (built-in: duplicate names + any sizes)",
    # --- TXD ---
    "Извлечение PNG из TXD (рекурсивно, с альфа-каналом)":
        "Extract PNG from TXD (recursive, with alpha)",
    "Папка с .txd:": "Folder with .txd:", "Папка вывода (PNG):": "Output folder (PNG):",
    "Извлечь PNG": "Extract PNG", "Упаковка PNG обратно в TXD": "Pack PNG back into TXD",
    "Папка с .txd (шаблоны):": "Folder with .txd (templates):",
    "Папка с PNG (рекурсивно):": "Folder with PNG (recursive):",
    "Папка для новых TXD:": "Folder for new TXD:",
    "Если размер PNG не совпадает с оригиналом": "If PNG size differs from original",
    "Дубликаты TXD (dojo__1 / dojo_2) > брать PNG из папки 'dojo' и вписывать под оригинал":
        "Duplicate TXD (dojo__1 / dojo_2) > take PNG from 'dojo' folder and fit to original",
    "Вписывать PNG в оригинальный размер — БЕЗОПАСНО": "Fit PNG to original size — SAFE",
    "Пропускать такие текстуры (оставлять оригинал)": "Skip such textures (keep original)",
    "Менять размер в TXD — ЭКСПЕРИМЕНТАЛЬНО": "Change size in TXD — EXPERIMENTAL",
    "Упаковать PNG в TXD": "Pack PNG into TXD", "Проверить размеры PNG": "Check PNG sizes",
    # --- PAC ---
    "Извлечение TXD из PAC (рекурсивно)": "Extract TXD from PAC (recursive)",
    "Папка с .pac:": "Folder with .pac:", "Извлечь TXD из PAC": "Extract TXD from PAC",
    "Замена TXD в PAC (только при совпадении размера)":
        "Replace TXD in PAC (only if size matches)",
    "Папка с новыми TXD:": "Folder with new TXD:",
    "Папка для патченных PAC:": "Folder for patched PAC:", "Запатчить PAC": "Patch PAC",
    # --- AUDIO ---
    "ADX > FLAC / WAV": "ADX > FLAC / WAV", "Папка с ADX:": "Folder with ADX:",
    "Папка для результата:": "Result folder:", "ADX > FLAC": "ADX > FLAC",
    "ADX > WAV": "ADX > WAV", "WAV / MP3 / FLAC > ADX": "WAV / MP3 / FLAC > ADX",
    "Папка с аудио:": "Audio folder:", "Папка для ADX:": "Folder for ADX:",
    "Приводить к формату игры: 24000 Hz / 16 bit / mono":
        "Convert to game format: 24000 Hz / 16 bit / mono",
    "В ADX": "To ADX",
    # --- DAT editor ---
    "1. Открыть оригинал .dat": "1. Open original .dat",
    "2. Загрузить TXT": "2. Load TXT", "3. Сохранить новый .dat": "3. Save new .dat",
    "(оригинал не выбран)": "(no original selected)",
    "p2 = len + 4 (фиксировано)": "p2 = len + 4 (fixed)",
    "  макс. длина:": "  max length:", "(0 = без лимита)": "(0 = no limit)",
    "Реплики:": "Lines:", "+ реплика": "+ line", "− реплика": "- line",
    "Текст реплики (только ЗАГЛАВНЫЕ, ⏎ = перенос):":
        "Line text (UPPERCASE only, ⏎ = newline):",
    "В файл ляжет:": "Will be written as:",
    "Применить (в память)": "Apply (in memory)", "Сброс к оригиналу": "Reset to original",
    "Готов": "Ready", "Готово": "Done",
    "1. Папка с оригинальными .dat:": "1. Folder with original .dat:",
    "2. TXT со сценарием:": "2. Script TXT:",
    "3. Папка для новых .dat:": "3. Folder for new .dat:",
    "ЭКСПОРТ ВСЕГО ТЕКСТА В TXT": "EXPORT ALL TEXT TO TXT",
    "ЗАПУСТИТЬ ЗАМЕНУ": "RUN REPLACEMENT",
    # --- EXE text ---
    "1. Открыть .exe": "1. Open .exe", "Экспорт TXT": "Export TXT", "Импорт TXT": "Import TXT",
    "2. Сохранить patched.exe": "2. Save patched.exe",
    "показать все строки (вкл. id/en)": "show all strings (incl. id/en)",
    "мин. длина:": "min length:", "(файл не выбран)": "(no file selected)",
    "Строки (RU = перевод, * = изменено):": "Strings (RU = translation, * = edited):",
    "Оригинал (из файла):": "Original (from file):",
    "Твой текст (Enter = перенос, теги <clut=N>/%s сохраняй):":
        "Your text (Enter = newline, keep <clut=N>/%s tags):",
    "Как ляжет в файл (шрифт-код):": "As stored in file (font code):",
    # --- log window ---
    "Лог (Ctrl+C — копировать, правый клик — меню)":
        "Log (Ctrl+C — copy, right click — menu)",
    "Лог": "Log", "Лог — TMNT Tool": "Log — TMNT Tool",
    "Копировать весь лог": "Copy whole log", "Сохранить лог…": "Save log…",
    "Отдельное окно": "Separate window", "Очистить": "Clear",
    "Копировать": "Copy", "Выделить всё": "Select all",
    "Сохранить лог в файл…": "Save log to file…",
    "Открыть в отдельном окне": "Open in separate window", "Очистить лог": "Clear log",
    "Копировать всё": "Copy all", "Копировать выделенное": "Copy selection",
    "Сохранить…": "Save…", "Очистить окно": "Clear window",
    "Автоскролл": "Auto-scroll", "Поверх всех": "Always on top",
    "Сохранить лог": "Save log",
    # --- dialogs ---
    "Ошибка": "Error", "Проверка": "Check", "Занято": "Busy",
    "Экспериментальный режим": "Experimental mode",
    "Нет реплик": "No lines", "Сначала оригинал": "Open original first",
    "Нет оригинала": "No original", "Ошибка сборки": "Build error",
    "Нет данных": "No data", "Пусто": "Empty", "Ошибка записи": "Write error",
    "Ошибка чтения TXT": "TXT read error", "Выбор сцены": "Choose scene",
    "Нет сцены": "No scene", "Переполнение длины": "Length overflow",
    "Дождись окончания операции": "Wait for the current operation to finish",
    "В файле не найдено реплик": "No lines found in this file",
    "Открой оригинальный .dat": "Open the original .dat first",
    "Сначала открой оригинал .dat": "Open the original .dat first",
    "Сначала открой оригинал": "Open the original first",
    "В папке нет .dat файлов": "No .dat files in this folder",
    "Укажи папку с оригинальными .dat": "Specify the folder with original .dat",
    "Укажи TXT со сценарием": "Specify the script TXT",
    "Укажи папку для новых .dat": "Specify the folder for new .dat",
    "Укажите папку для результата": "Specify the result folder",
    "Укажите существующую папку с исходными файлами":
        "Specify an existing folder with source files",
    "Менять размеры текстур в TXD — рискованно.\nВсё равно продолжить?":
        "Changing texture sizes in TXD is risky.\nContinue anyway?",
    # --- misc ---
    "Язык / Language:": "Язык / Language:",
    "(интерфейс переводится сразу)": "(interface switches instantly)",
}

_PHRASES = [
    ("Укажите папку с", "Specify the folder with"),
    ("Укажи папку с", "Specify the folder with"),
    ("Укажите файл", "Specify the file"),
    ("Укажите папку вывода", "Specify the output folder"),
    ("Укажите папку для", "Specify the folder for"),
    ("Укажи папку для", "Specify the folder for"),
    ("не найден рядом со скриптом", "not found next to the script"),
    ("Обрабатывать подпапки", "Process subfolders"),
    ("Пропускать уже готовые", "Skip already processed"),
    ("Пакетная обработка видео", "Batch video processing"),
    ("Пакетная замена аудио", "Batch audio replacement"),
    ("Пакетное извлечение аудио", "Batch audio extraction"),
    ("Заменить аудио в SFD", "Replace audio in SFD"),
    ("Извлечь аудио (ADX)", "Extract audio (ADX)"),
    ("Извлечь аудио", "Extract audio"),
    ("Инструменты", "Tools"), ("Одиночный файл", "Single file"),
    ("Исходный SFD:", "Source SFD:"), ("Новое аудио:", "New audio:"),
    ("Выходной SFD:", "Output SFD:"), ("Папка с SFD:", "Folder with SFD:"),
    ("Папка с аудио:", "Audio folder:"), ("Папка вывода:", "Output folder:"),
    ("Заменить аудио", "Replace audio"), ("Инфо о SFD", "SFD info"),
    ("Стоп", "Stop"), ("Найти", "Search"), ("Сканировать", "Scan"),
    ("Имя текстуры (подстрока):", "Texture name (substring):"),
    ("показать все записи", "show all records"),
    ("мин. оценка:", "min score:"),
    ("Текстура", "Texture"), ("Смещение", "Offset"), ("Раскладка", "Layout"),
    ("Оценка", "Score"), ("Правка", "Edit"), ("Записей:", "Records:"),
    ("показано", "shown"),
    ("Текстура из TXD (только справка):", "Texture from TXD (reference only):"),
    ("нет данных TXD", "no TXD data"),
    ("Размер квада в EXE (это и меняем):", "Quad size in EXE (this is what you edit):"),
    ("было:", "was:"), ("Масштаб от текстуры:", "Scale from texture:"),
    ("w,h = текстура × k", "w,h = texture × k"),
    ("Применить правку", "Apply edit"), ("Сбросить запись", "Reset record"),
    ("Сохранить новый EXE…", "Save new EXE…"),
    ("Отменить все правки", "Discard all edits"),
    ("Байты записи (24):", "Record bytes (24):"),
    ("Правленых записей:", "Edited records:"),
    ("EXE (оригинал):", "EXE (original):"),
    ("Папка TXD (необяз.):", "TXD folder (optional):"),
    ("Извлечено", "Extracted"), ("Извлечён", "Extracted"),
    ("Пересобрано", "Rebuilt"), ("Пересобран", "Rebuilt"),
    ("Сконвертировано", "Converted"), ("Заменено", "Replaced"),
    ("заменено", "replaced"), ("Записано", "Written"),
    ("Изменено", "Changed"), ("изменено", "changed"),
    ("Загружено", "Loaded"), ("Сохранено", "Saved"),
    ("Найдено", "Found"), ("найдено", "found"),
    ("Пропущено", "Skipped"), ("пропущено", "skipped"),
    ("пропуск", "skip"), ("Пропущен", "Skipped"),
    ("Успешно", "OK"), ("Ошибок", "Errors"), ("ошибок", "errors"),
    ("Ошибка", "Error"), ("ошибка", "error"),
    ("ГОТОВО", "DONE"), ("Готово", "Done"),
    ("Извлечение", "Extraction"), ("Замена", "Replacement"),
    ("Пересборка", "Rebuild"), ("Упаковка", "Packing"),
    ("Проверка", "Check"), ("Запуск", "Running"),
    ("Оригинал", "Original"), ("оригинал", "original"),
    ("Новый", "New"), ("новый", "new"),
    ("Длительность", "Duration"), ("Заголовок", "Header"),
    ("Подготовка звука", "Preparing audio"),
    ("Кодирование в ADX", "Encoding to ADX"),
    ("ресемплинг + добивка тишиной", "resample + pad with silence"),
    ("Аудиопоток не найден", "Audio stream not found"),
    ("Аудио", "Audio"), ("аудио", "audio"),
    ("пакетов", "packets"), ("пакеты", "packets"),
    ("байт всего", "bytes total"), ("байт", "bytes"), ("Байт", "Bytes"),
    ("файлов", "files"), ("файл", "file"), ("архивов", "archives"),
    ("реплик", "lines"), ("Сцен в TXT", "Scenes in TXT"),
    ("Текстур", "Textures"), ("текстур", "textures"),
    ("хэш совпал", "hash matches"), ("совпадает", "matches"),
    ("палитра", "palette"), ("точная", "exact"), ("квантованная", "quantized"),
    ("уровней альфы", "alpha levels"), ("с альфой", "with alpha"),
    ("полупрозрачных градаций меньше", "fewer semi-transparent levels"),
    ("размер файла не меняется", "file size unchanged"),
    ("размер", "size"), ("Размер", "Size"),
    ("Папка", "Folder"), ("папка", "folder"),
    ("добавлено", "added"), ("данных", "data"),
    ("нет изменений", "no changes"), ("Нет замен", "Nothing replaced"),
    ("копия не создана", "no copy created"),
    ("Оригинал не изменён", "Original untouched"),
    ("Поле", "Field"), ("не число", "not a number"),
    ("слишком большое", "too large"), ("должно быть", "must be"),
    ("Запись", "Record"), ("запись", "record"),
    ("Не удалось", "Failed"), ("Проверь", "Check"),
    ("Обработка", "Processing"), ("Пропускаю", "Skipping"),
    ("нет аудио", "no audio"), ("Готов", "Ready"),
]
_PHRASE_RE = [(re.compile(r"(?<!\w)" + re.escape(a) + r"(?!\w)"), b)
              for a, b in sorted(_PHRASES, key=lambda x: -len(x[0]))]


def tr_text(s):
    if LANG == "ru" or not s:
        return s
    for rx, en in _PHRASE_RE:
        s = rx.sub(en, s)
    return s


def tr(s):
    if LANG == "ru" or not s:
        return s
    hit = TR.get(s)
    if hit is not None:
        return hit
    return tr_text(s)


_TEXT_CLASSES = {"TLabel", "Label", "TButton", "Button", "TCheckbutton", "Checkbutton",
                 "TRadiobutton", "Radiobutton", "TLabelframe", "Labelframe",
                 "TMenubutton", "Menubutton"}


def translate_tree(w):
    try:
        cls = w.winfo_class()
    except Exception:
        return
    try:
        if cls in _TEXT_CLASSES:
            key = (str(w), "text")
            if key not in ORIG:
                ORIG[key] = w.cget("text")
            o = ORIG[key]
            if o:
                t = tr(o)
                if t != w.cget("text"):
                    w.configure(text=t)
        elif cls == "TNotebook":
            for i in range(w.index("end")):
                key = (str(w), "tab", i)
                if key not in ORIG:
                    ORIG[key] = w.tab(i, "text")
                o = ORIG[key]
                t = tr(o)
                if t != w.tab(i, "text"):
                    w.tab(i, text=t)
        elif cls == "Menu":
            n = w.index("end")
            if n is not None:
                for i in range(n + 1):
                    try:
                        lbl = w.entrycget(i, "label")
                    except Exception:
                        continue
                    key = (str(w), "menu", i)
                    if key not in ORIG:
                        ORIG[key] = lbl
                    o = ORIG[key]
                    if o:
                        t = tr(o)
                        if t != lbl:
                            w.entryconfigure(i, label=t)
        elif cls in ("Tk", "Toplevel"):
            key = (str(w), "title")
            if key not in ORIG:
                ORIG[key] = w.title()
            o = ORIG[key]
            if o:
                t = tr(o)
                if t != w.title():
                    w.title(t)
        if isinstance(w, ttk.Treeview):
            for c in w["columns"]:
                key = (str(w), "head", c)
                if key not in ORIG:
                    ORIG[key] = w.heading(c, "text")
                o = ORIG[key]
                if o:
                    t = tr(o)
                    if t != w.heading(c, "text"):
                        w.heading(c, text=t)
    except Exception:
        pass
    try:
        kids = w.winfo_children()
    except Exception:
        kids = []
    for c in kids:
        translate_tree(c)


def _patch_dialogs():
    def wrap_msg(fn):
        def f(title=None, message=None, **kw):
            return fn(tr(title), tr_text(message), **kw)
        return f

    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel"):
        setattr(messagebox, name, wrap_msg(getattr(messagebox, name)))

    def wrap_dlg(fn):
        def f(**kw):
            if "title" in kw:
                kw["title"] = tr(kw["title"])
            return fn(**kw)
        return f

    for name in ("askdirectory", "askopenfilename", "asksaveasfilename"):
        setattr(filedialog, name, wrap_dlg(getattr(filedialog, name)))


def load_cfg():
    global LANG
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            LANG = json.load(f).get("lang", "ru")
    except Exception:
        LANG = "ru"


def save_cfg():
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump({"lang": LANG}, f)
    except Exception:
        pass


def set_lang(value, root):
    global LANG
    LANG = "en" if str(value).lower().startswith("eng") else "ru"
    save_cfg()
    translate_tree(root)


def _tick(root):
    try:
        if LANG != "ru":
            translate_tree(root)
        root.after(1500, lambda: _tick(root))
    except Exception:
        pass


# ============================================================================
# ==================  EXE: СПРАЙТЫ — РЕДАКТОР ПО ИМЕНИ  ======================
# ============================================================================
SP_SECS = (".rdata", ".data")
SP_IDENT = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
SP_LAYOUTS = [
    ("I24p20", 24, 20, [(0, 'i', 'x'), (4, 'i', 'y'), (8, 'I', 'w'), (12, 'I', 'h'), (16, 'I', 'flags')]),
    ("F24p4",  24,  4, [(0, 'I', 'flags'), (8, 'f', 'x'), (12, 'f', 'y'), (16, 'f', 'w'), (20, 'f', 'h')]),
    ("I24p0",  24,  0, [(4, 'i', 'x'), (8, 'i', 'y'), (12, 'I', 'w'), (16, 'I', 'h'), (20, 'I', 'flags')]),
    ("F24p20", 24, 20, [(0, 'f', 'x'), (4, 'f', 'y'), (8, 'f', 'w'), (12, 'f', 'h'), (16, 'I', 'flags')]),
]
SP_LAY = {l[0]: l for l in SP_LAYOUTS}
SP_PRIO = {"I24p20": 0, "F24p4": 1, "F24p20": 2, "I24p0": 3}


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


def sp_sec_of(off, secs):
    for nm, rp, rsz, va, vsz in secs:
        if rp <= off < rp + rsz:
            return nm
    return None


def sp_names(data, base, secs, wanted):
    pat = re.compile(rb"[A-Za-z][A-Za-z0-9_\-]{2,39}\x00")
    out = {}
    for m in pat.finditer(data):
        s = m.start()
        if s > 0 and data[s - 1] in SP_IDENT:
            continue
        if sp_sec_of(s, secs) not in SP_SECS:
            continue
        nm = m.group()[:-1].decode("ascii")
        if wanted is not None and nm.lower() not in wanted:
            continue
        va = sp_off2va(s, base, secs)
        if va:
            out[va] = nm
    return out


def sp_txd_map(root):
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
                first, bad = None, False
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


def sp_decode(layout, buf, start):
    vals = {}
    for off, t, nm in layout[3]:
        vals[nm] = struct.unpack_from("<" + t, buf, start + off)[0]
    return vals


def sp_plausible(v, dim):
    if dim:
        return 0 < v <= 4096
    return abs(v) <= 8192


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


def sp_scan(data, base, secs, names, texmap, min_score):
    """Поиск записей + отсев ПЕРЕКРЫВАЮЩИХСЯ фантомов (главный фикс)."""
    cands = []
    for nm, rp, rsz, va, vsz in secs:
        if nm not in SP_SECS:
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
                s = p - ptroff
                if s < 0 or s + size > len(data):
                    continue
                if sp_sec_of(s, secs) not in SP_SECS:
                    continue
                try:
                    vals = sp_decode(layout, data, s)
                except struct.error:
                    continue
                sc = sp_score(vals, tex)
                if sc < min_score:
                    continue
                cands.append((sc, s, lid, texname, v, tex))
    cands.sort(key=lambda c: (-c[0], c[1], SP_PRIO[c[2]]))
    taken, out = [], []
    for sc, s, lid, texname, ptr, tex in cands:
        e = s + 24
        if any(not (e <= a or s >= b) for a, b in taken):
            continue
        taken.append((s, e))
        out.append({'off': s, 'va': sp_off2va(s, base, secs) or 0, 'layout': lid,
                    'name': texname, 'ptr': ptr, 'score': sc, 'tex': tex,
                    'raw': bytes(data[s:e])})
    out.sort(key=lambda r: (r['name'].lower(), r['off']))
    return out


class SpriteEditorTab:
    """Редактор спрайтов EXE: поиск по имени, правка только выбранной записи."""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.exe = tk.StringVar()
        self.txd = tk.StringVar()
        self.query = tk.StringVar()
        self.show_all = tk.BooleanVar(value=True)
        self.min_score = tk.IntVar(value=2)
        self.scale = tk.StringVar(value="1.0")
        self.lay = tk.StringVar()
        self.vars = {k: tk.StringVar() for k in ("x", "y", "w", "h", "flags")}
        self.data = None
        self.base = None
        self.secs = None
        self.recs = []
        self.by_off = {}
        self.edits = {}
        self.cur = None
        self._q = queue.Queue()
        self._busy = False
        self._btns = []
        self._ui()
        self.frame.after(100, self._pump)

    # ---------------- UI ----------------
    def _ui(self):
        top = ttk.LabelFrame(self.frame, text="EXE (оригинал):", padding=6)
        top.pack(fill="x", padx=6, pady=5)
        ttk.Entry(top, textvariable=self.exe).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(top, text="...", width=4, command=self.pick_exe).grid(row=0, column=1)
        b = ttk.Button(top, text="Сканировать", command=self.do_scan)
        b.grid(row=0, column=2, padx=(6, 0))
        self._btns.append(b)
        ttk.Label(top, text="Папка TXD (необяз.):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        row = ttk.Frame(top)
        row.grid(row=2, column=0, columnspan=3, sticky="ew")
        ttk.Entry(row, textvariable=self.txd).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="...", width=4, command=self.pick_txd).pack(side="left", padx=(4, 0))
        top.columnconfigure(0, weight=1)

        sr = ttk.Frame(self.frame)
        sr.pack(fill="x", padx=6)
        ttk.Label(sr, text="Имя текстуры (подстрока):").pack(side="left")
        e = ttk.Entry(sr, textvariable=self.query, width=28)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.refresh_list())
        ttk.Button(sr, text="Найти", command=self.refresh_list).pack(side="left")
        ttk.Checkbutton(sr, text="показать все записи", variable=self.show_all,
                        command=self.refresh_list).pack(side="left", padx=10)
        ttk.Label(sr, text="мин. оценка:").pack(side="left")
        tk.Spinbox(sr, from_=1, to=9, textvariable=self.min_score, width=3).pack(side="left")
        self.count_lbl = ttk.Label(sr, text="Записей: 0")
        self.count_lbl.pack(side="right")

        main = tk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        main.pack(fill="both", expand=True, padx=6, pady=6)

        left = ttk.Frame(main)
        main.add(left, width=620)
        cols = ("name", "off", "lay", "size", "score", "mod")
        heads = {"name": "Текстура", "off": "Смещение", "lay": "Раскладка",
                 "size": "w × h", "score": "Оценка", "mod": "Правка"}
        widths = {"name": 210, "off": 100, "lay": 80, "size": 110, "score": 60, "mod": 60}
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="w")
        vs = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.tag_configure("edited", background="#fff2c0")

        right = ttk.Frame(main)
        main.add(right)
        self.info = ttk.Label(right, text="(запись не выбрана)", anchor="w", foreground="gray")
        self.info.pack(anchor="w")
        self.texinfo = ttk.Label(right, text="Текстура из TXD (только справка): —",
                                 anchor="w", foreground="#0a6")
        self.texinfo.pack(anchor="w", pady=(2, 4))

        lr = ttk.Frame(right)
        lr.pack(fill="x")
        ttk.Label(lr, text="Раскладка").pack(side="left")
        self.lay_box = ttk.Combobox(lr, textvariable=self.lay, state="readonly", width=10)
        self.lay_box.pack(side="left", padx=6)
        self.lay_box.bind("<<ComboboxSelected>>", self.on_layout)

        ttk.Label(right, text="Размер квада в EXE (это и меняем):").pack(anchor="w", pady=(6, 2))
        grid = ttk.Frame(right)
        grid.pack(fill="x")
        self.orig_lbls = {}
        for i, fn in enumerate(("x", "y", "w", "h", "flags")):
            ttk.Label(grid, text=fn, width=7).grid(row=i, column=0, sticky="w")
            ttk.Entry(grid, textvariable=self.vars[fn], width=16).grid(row=i, column=1, sticky="w")
            l = ttk.Label(grid, text="было: —", foreground="gray")
            l.grid(row=i, column=2, sticky="w", padx=8)
            self.orig_lbls[fn] = l

        sc = ttk.Frame(right)
        sc.pack(fill="x", pady=(6, 0))
        ttk.Label(sc, text="Масштаб от текстуры:").pack(side="left")
        ttk.Entry(sc, textvariable=self.scale, width=8).pack(side="left", padx=4)
        ttk.Button(sc, text="w,h = текстура × k", command=self.apply_scale).pack(side="left")

        ttk.Label(right, text="Байты записи (24):").pack(anchor="w", pady=(6, 0))
        self.hexbox = tk.Text(right, height=7, wrap="word", state="disabled",
                              bg="#f2f2f2", font=("Consolas", 9))
        self.hexbox.pack(fill="x")

        bb = ttk.Frame(right)
        bb.pack(fill="x", pady=6)
        ttk.Button(bb, text="Применить правку", command=self.apply_current).pack(side="left")
        ttk.Button(bb, text="Сбросить запись", command=self.reset_current).pack(side="left", padx=6)

        bot = ttk.Frame(self.frame)
        bot.pack(fill="x", padx=6)
        b1 = ttk.Button(bot, text="Сохранить новый EXE…", command=self.do_save)
        b1.pack(side="left")
        self._btns.append(b1)
        ttk.Button(bot, text="Отменить все правки", command=self.drop_edits).pack(side="left", padx=6)
        self.ed_lbl = ttk.Label(bot, text="Правленых записей: 0")
        self.ed_lbl.pack(side="right")

        lf = ttk.LabelFrame(self.frame, text="Лог", padding=4)
        lf.pack(fill="both", expand=False, padx=6, pady=(4, 6))
        self.logw = scrolledtext.ScrolledText(lf, height=8, wrap="none", font=("Consolas", 9))
        self.logw.pack(fill="both", expand=True)

    # ---------------- helpers ----------------
    def pick_exe(self):
        p = filedialog.askopenfilename(parent=self.frame, title="EXE (оригинал):",
                                       filetypes=[("EXE", "*.exe"), ("All", "*.*")])
        if p:
            self.exe.set(p)

    def pick_txd(self):
        p = filedialog.askdirectory(parent=self.frame, title="Папка TXD (необяз.):")
        if p:
            self.txd.set(p)

    def log(self, m):
        self._q.put(("log", str(m)))

    def _pump(self):
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "log":
                    self.logw.insert("end", tr_text(val) + "\n")
                    self.logw.see("end")
                elif kind == "recs":
                    self.recs = val
                    self.by_off = {r['off']: r for r in val}
                    self.edits = {}
                    self.cur = None
                    self.refresh_list()
                elif kind == "done":
                    self._busy = False
                    for b in self._btns:
                        b.configure(state="normal")
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
            b.configure(state="disabled")

        def worker():
            try:
                fn()
            except Exception as e:
                self.log("[!!] %s" % e)
            finally:
                self._q.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- scan ----------------
    def do_scan(self):
        exe = self.exe.get().strip()
        txd = self.txd.get().strip()
        if not exe or not os.path.isfile(exe):
            messagebox.showerror("Ошибка", "Укажите папку с .exe", parent=self.frame)
            return
        if txd and not os.path.isdir(txd):
            messagebox.showerror("Ошибка", "Папка TXD не найдена", parent=self.frame)
            return
        try:
            ms = int(self.min_score.get())
        except Exception:
            ms = 2
        self.logw.delete("1.0", "end")
        self.log("=== EXE: поиск спрайтов ===")
        self.log(exe)

        def job():
            with open(exe, "rb") as f:
                data = f.read()
            base, secs = sp_pe_info(data)
            texmap = sp_txd_map(txd) if txd else None
            if texmap is not None:
                self.log("Имён текстур из TXD: %d" % len(texmap))
            wanted = set(texmap.keys()) if texmap else None
            names = sp_names(data, base, secs, wanted)
            self.log("Строк-кандидатов: %d" % len(names))
            recs = sp_scan(data, base, secs, names, texmap, ms)
            self.data, self.base, self.secs = data, base, secs
            self.log("Найдено записей: %d (перекрывающиеся фантомы отброшены)" % len(recs))
            self._q.put(("recs", recs))

        self._start(job)

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        q = self.query.get().strip().lower()
        shown = 0
        for r in self.recs:
            if not self.show_all.get() and q and q not in r['name'].lower():
                continue
            if q and q not in r['name'].lower() and not self.show_all.get():
                continue
            vals = sp_decode(SP_LAY[r['layout']], r['raw'], 0)
            ed = self.edits.get(r['off'])
            if ed:
                vals.update(ed['fields'])
                lid = ed['layout']
            else:
                lid = r['layout']
            size = "%g × %g" % (vals['w'], vals['h'])
            iid = "0x%08X" % r['off']
            self.tree.insert("", "end", iid=iid,
                             values=(r['name'], iid, lid, size, r['score'], "*" if ed else ""),
                             tags=("edited",) if ed else ())
            shown += 1
        self.count_lbl.configure(text="Записей: %d / показано %d" % (len(self.recs), shown))
        self.ed_lbl.configure(text="Правленых записей: %d" % len(self.edits))

    def valid_layouts(self, r):
        out = []
        for lid, size, poff, _ in SP_LAYOUTS:
            s = r['off']
            if s + size > len(self.data):
                continue
            if struct.unpack_from("<I", self.data, s + poff)[0] == r['ptr']:
                out.append(lid)
        return out or [r['layout']]

    def on_select(self, e=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.cur = int(sel[0], 16)
        self.load_record()

    def load_record(self):
        r = self.by_off.get(self.cur)
        if not r:
            return
        lay_list = self.valid_layouts(r)
        self.lay_box.configure(values=lay_list)
        ed = self.edits.get(r['off'])
        lid = ed['layout'] if ed else r['layout']
        if lid not in lay_list:
            lid = lay_list[0]
        self.lay.set(lid)
        self.info.configure(text="%s   off=0x%08X  va=0x%08X  ptr=0x%08X  score=%d"
                                 % (r['name'], r['off'], r['va'], r['ptr'], r['score']))
        if r['tex']:
            tw, th, bpp = r['tex']
            self.texinfo.configure(text="Текстура из TXD (только справка): %d × %d, bpp=%d"
                                        % (tw, th, bpp))
        else:
            self.texinfo.configure(text="Текстура из TXD (только справка): нет данных TXD")
        self.fill_fields()

    def fill_fields(self):
        r = self.by_off.get(self.cur)
        if not r:
            return
        lay = SP_LAY[self.lay.get()]
        base_vals = sp_decode(lay, r['raw'], 0)
        ed = self.edits.get(r['off'])
        cur = dict(base_vals)
        if ed and ed['layout'] == self.lay.get():
            cur.update(ed['fields'])
        for fn in ("x", "y", "w", "h", "flags"):
            self.vars[fn].set("%g" % cur[fn] if isinstance(cur[fn], float) else str(cur[fn]))
            self.orig_lbls[fn].configure(text="было: %g" % base_vals[fn]
                                         if isinstance(base_vals[fn], float)
                                         else "было: %d" % base_vals[fn])
        txt = [" ".join("%02X" % b for b in r['raw'])]
        for lid in self.valid_layouts(r):
            v = sp_decode(SP_LAY[lid], r['raw'], 0)
            txt.append("%-7s x=%g y=%g w=%g h=%g flags=%g"
                       % (lid, v['x'], v['y'], v['w'], v['h'], v['flags']))
        self.hexbox.configure(state="normal")
        self.hexbox.delete("1.0", "end")
        self.hexbox.insert("1.0", "\n".join(txt))
        self.hexbox.configure(state="disabled")

    def on_layout(self, e=None):
        if self.cur is None:
            return
        if self.cur in self.edits:
            del self.edits[self.cur]
            self.log("Раскладка изменена — правка записи сброшена")
        self.fill_fields()
        self.refresh_list()

    def apply_scale(self):
        r = self.by_off.get(self.cur) if self.cur is not None else None
        if not r or not r['tex']:
            messagebox.showwarning("Нет данных", "нет данных TXD", parent=self.frame)
            return
        try:
            k = float(self.scale.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Ошибка", "Масштаб: не число", parent=self.frame)
            return
        tw, th, _ = r['tex']
        lay = SP_LAY[self.lay.get()]
        types = {n: t for _, t, n in lay[3]}
        for fn, base in (("w", tw), ("h", th)):
            v = base * k
            self.vars[fn].set("%d" % round(v) if types[fn] != 'f' else "%.6g" % v)

    def apply_current(self):
        if self.cur is None:
            return
        r = self.by_off[self.cur]
        lid = self.lay.get()
        if lid not in self.valid_layouts(r):
            messagebox.showerror("Ошибка", "Раскладка не подходит для этой записи",
                                 parent=self.frame)
            return
        lay = SP_LAY[lid]
        base_vals = sp_decode(lay, r['raw'], 0)
        fields = {}
        for off, t, fn in lay[3]:
            s = self.vars[fn].get().strip().replace(",", ".")
            if s == "":
                continue
            try:
                num = float(s)
            except ValueError:
                messagebox.showerror("Ошибка", "Поле %s: не число" % fn, parent=self.frame)
                return
            if t == 'f':
                new = float(num)
                if not (-1e6 < new < 1e6):
                    messagebox.showerror("Ошибка", "Поле %s: слишком большое" % fn,
                                         parent=self.frame)
                    return
            else:
                new = int(round(num))
                if t == 'I' and not (0 <= new <= 0xFFFFFFFF):
                    messagebox.showerror("Ошибка", "Поле %s: должно быть 0..4294967295" % fn,
                                         parent=self.frame)
                    return
                if t == 'i' and not (-2 ** 31 <= new < 2 ** 31):
                    messagebox.showerror("Ошибка", "Поле %s: слишком большое" % fn,
                                         parent=self.frame)
                    return
            if fn in ("w", "h") and not (0 < new <= 4096):
                messagebox.showerror("Ошибка", "Поле %s: должно быть 1..4096" % fn,
                                     parent=self.frame)
                return
            if fn == "flags" and not (0 <= new <= 0xFFFF):
                messagebox.showerror("Ошибка", "Поле flags: должно быть 0..65535",
                                     parent=self.frame)
                return
            if new != base_vals[fn]:
                fields[fn] = new
        if not fields and lid == r['layout']:
            self.edits.pop(r['off'], None)
            self.log("0x%08X %s: нет изменений" % (r['off'], r['name']))
        else:
            self.edits[r['off']] = {'layout': lid, 'fields': fields}
            self.log("0x%08X %s: %s" % (r['off'], r['name'],
                                        ", ".join("%s=%g" % (k, v) for k, v in fields.items())
                                        or "смена раскладки"))
        self.refresh_list()
        try:
            self.tree.selection_set("0x%08X" % r['off'])
        except Exception:
            pass

    def reset_current(self):
        if self.cur is None:
            return
        self.edits.pop(self.cur, None)
        self.load_record()
        self.refresh_list()

    def drop_edits(self):
        self.edits = {}
        if self.cur is not None:
            self.load_record()
        self.refresh_list()
        self.log("Все правки отменены")

    def do_save(self):
        if not self.data:
            messagebox.showerror("Ошибка", "Сначала сканируй EXE", parent=self.frame)
            return
        if not self.edits:
            messagebox.showwarning("Нет данных", "нет изменений", parent=self.frame)
            return
        src = self.exe.get().strip()
        out = filedialog.asksaveasfilename(
            parent=self.frame, title="Сохранить новый EXE…",
            initialdir=os.path.dirname(src),
            initialfile=os.path.splitext(os.path.basename(src))[0] + "_sprites.exe",
            defaultextension=".exe", filetypes=[("EXE", "*.exe"), ("All", "*.*")])
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(src):
            messagebox.showerror("Ошибка", "Нельзя писать поверх оригинала", parent=self.frame)
            return
        buf = bytearray(self.data)
        nf = 0
        for off, ed in sorted(self.edits.items()):
            r = self.by_off[off]
            lay = SP_LAY[ed['layout']]
            if struct.unpack_from("<I", buf, off + lay[2])[0] != r['ptr']:
                messagebox.showerror("Ошибка", "Запись 0x%08X повреждена, пропуск" % off,
                                     parent=self.frame)
                continue
            for fn, val in ed['fields'].items():
                foff, t = [(o, t) for o, t, n in lay[3] if n == fn][0]
                struct.pack_into("<" + t, buf, off + foff, val)
                nf += 1
        nb = sum(1 for a, b in zip(self.data, buf) if a != b)
        if nb == 0:
            messagebox.showinfo("Готово", "нет изменений", parent=self.frame)
            return
        with open(out, "wb") as f:
            f.write(buf)
        self.log("Сохранено: %s" % out)
        self.log("Записей: %d, полей: %d, изменено байт: %d. Оригинал не изменён."
                 % (len(self.edits), nf, nb))
        messagebox.showinfo("Готово",
                            "Сохранено:\n%s\n\nЗаписей: %d\nПолей: %d\nБайт изменено: %d"
                            % (out, len(self.edits), nf, nb), parent=self.frame)


# ============================================================================
# ==============================  SFD  =======================================
# ============================================================================
SFD_SECTOR = 0x800


def _exe_or_path(name):
    p = os.path.join(SCRIPT_DIR, name + ".exe")
    return p if os.path.isfile(p) else name


DEF_FFMPEG = _exe_or_path("ffmpeg")
DEF_FFPROBE = _exe_or_path("ffprobe")
DEF_RADX = os.path.join(SCRIPT_DIR, "tools", "radx_encode.exe")


def sfd_pack_len(buf, pos):
    b = buf[pos + 4]
    if (b & 0xF0) == 0x20:
        return 12
    if (b & 0xC0) == 0x40:
        return 14 + (buf[pos + 13] & 7)
    raise ValueError("неизвестный pack header на %#x" % pos)


def sfd_pes_payload(buf, off):
    sid = buf[off + 3]
    plen = int.from_bytes(buf[off + 4:off + 6], "big")
    p, end = off + 6, off + 6 + plen
    if sid in (0xBB, 0xBC, 0xBE, 0xBF):
        return sid, p, end
    if (buf[p] & 0xC0) == 0x80:
        p += 3 + buf[p + 2]
    else:
        n = 0
        while p < end and buf[p] == 0xFF and n < 16:
            p += 1
            n += 1
        if p < end and (buf[p] & 0xC0) == 0x40:
            p += 2
        if p < end:
            m = buf[p] & 0xF0
            if m == 0x20:
                p += 5
            elif m == 0x30:
                p += 10
            elif buf[p] == 0x0F:
                p += 1
    return sid, p, end


def sfd_scan_audio(buf, aid):
    chunks, pos = [], 0
    while pos + SFD_SECTOR <= len(buf):
        if buf[pos:pos + 4] == b"\x00\x00\x01\xb9":
            break
        if buf[pos:pos + 4] != b"\x00\x00\x01\xba":
            pos += SFD_SECTOR
            continue
        try:
            off, stop = pos + sfd_pack_len(buf, pos), pos + SFD_SECTOR
            while off + 6 <= stop and buf[off:off + 3] == b"\x00\x00\x01":
                sid, s, e = sfd_pes_payload(buf, off)
                if e <= off or e > stop:
                    break
                if sid == aid:
                    chunks.append((s, e))
                off = e
        except Exception:
            pass
        pos += SFD_SECTOR
    return chunks


def sfd_audio_ids(buf):
    ids = set()
    for pos in range(0, len(buf) - SFD_SECTOR + 1, SFD_SECTOR):
        if buf[pos:pos + 4] != b"\x00\x00\x01\xba":
            continue
        try:
            off = pos + sfd_pack_len(buf, pos)
            stop = pos + SFD_SECTOR
            while off + 6 <= stop and buf[off:off + 3] == b"\x00\x00\x01":
                sid, s, e = sfd_pes_payload(buf, off)
                if e <= off or e > stop:
                    break
                if 0xC0 <= sid <= 0xDF:
                    ids.add(sid)
                off = e
        except Exception:
            continue
    return sorted(ids)


class SfdTool:
    AUDIO_IN = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".adx", ".wma")

    @staticmethod
    def _run(cmd, log, cwd=None):
        log("  > " + " ".join('"%s"' % c if " " in str(c) else str(c) for c in cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "exit %d" % r.returncode).strip())
        return r

    @staticmethod
    def _probe(path, ffprobe):
        try:
            r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                                "-show_streams", "-show_format", path],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            return json.loads(r.stdout or "{}")
        except Exception:
            return {}

    @staticmethod
    def _sfa(data, aid=None):
        aids = sfd_audio_ids(data)
        if not aids:
            raise RuntimeError("Аудиопоток не найден")
        if aid is None:
            aid = aids[0]
        chunks = sfd_scan_audio(data, aid)
        if not chunks:
            raise RuntimeError("Аудиопоток не найден: %s" % hex(aid))
        sfa = b"".join(bytes(data[s:e]) for s, e in chunks)
        hdr_size = int.from_bytes(sfa[2:4], "big") + 4
        return aids, aid, chunks, sfa, hdr_size

    @staticmethod
    def info(path, log, ffprobe):
        data = Path(path).read_bytes()
        aids, aid, chunks, sfa, hdr = SfdTool._sfa(data)
        sr = int.from_bytes(sfa[8:12], "big")
        ch = sfa[7]
        log("Файл: %s (%d байт)" % (path, len(data)))
        log("Аудиодорожки: %s (беру %s)" % ([hex(a) for a in aids], hex(aid)))
        log("Пакетов: %d, аудио байт: %d, заголовок: %d" % (len(chunks), len(sfa), hdr))
        log("ADX: %d Гц, %d кан., битность %d" % (sr, ch, sfa[6]))
        bps = sr / 32 * 18 * ch
        if bps:
            log("Длительность аудио: %.2f с" % ((len(sfa) - hdr) / bps))
        nfo = SfdTool._probe(path, ffprobe)
        for s in nfo.get("streams", []):
            if s.get("codec_type") == "video":
                log("Видео: %s %sx%s %s fps" % (s.get("codec_name"), s.get("width"),
                                                s.get("height"), s.get("avg_frame_rate")))
            elif s.get("codec_type") == "audio":
                log("Аудио (ffprobe): %s %s Гц %s кан."
                    % (s.get("codec_name"), s.get("sample_rate"), s.get("channels")))
        return True

    @staticmethod
    def extract(path, out_adx, log, aid=None):
        data = Path(path).read_bytes()
        aids, aid, chunks, sfa, hdr = SfdTool._sfa(data, aid)
        os.makedirs(os.path.dirname(os.path.abspath(out_adx)), exist_ok=True)
        Path(out_adx).write_bytes(sfa)
        log("Извлечено %d байт (%s, %d пакетов) -> %s"
            % (len(sfa), hex(aid), len(chunks), out_adx))
        return len(sfa)

    @staticmethod
    def replace(orig, audio, out, log, ffmpeg, radx, ffprobe=None, aid=None):
        data = bytearray(Path(orig).read_bytes())
        aids, aid, chunks, sfa, hdr_size = SfdTool._sfa(data, aid)
        header = sfa[:hdr_size]
        target = len(sfa) - hdr_size
        sr = int.from_bytes(header[8:12], "big")
        ch = header[7]
        if len(aids) > 1:
            log("  ВНИМАНИЕ: дорожек %d, меняю %s" % (len(aids), hex(aid)))
        log("  Оригинал: %d Гц, %d кан., пакетов %d, данных %d байт"
            % (sr, ch, len(chunks), target))
        if header[0x11A:0x120] != b"(c)CRI":
            log("  (CRI-метка не совпала — оставляю заголовок как в оригинале)")
        bps = sr / 32.0 * 18 * ch
        need_sec = target / bps + 0.25
        work = tempfile.mkdtemp(prefix="sfd_")
        try:
            if audio.lower().endswith(".adx"):
                adx = Path(audio).read_bytes()
                log("  Источник уже ADX — кодирование пропущено")
            else:
                if not os.path.isfile(radx) and shutil.which(radx) is None:
                    raise RuntimeError("radx_encode.exe не найден: %s" % radx)
                wav = os.path.join(work, "new.wav")
                log("  Подготовка звука: %.2f с (с запасом тишины)" % need_sec)
                SfdTool._run([ffmpeg, "-y", "-loglevel", "error", "-i", audio,
                              "-ar", str(sr), "-ac", str(ch), "-af", "apad",
                              "-t", "%.3f" % need_sec, "-c:a", "pcm_s16le", wav], log, work)
                adx_path = os.path.join(work, "new.adx")
                log("  Кодирование в ADX")
                SfdTool._run([radx, wav, adx_path], log, work)
                adx = Path(adx_path).read_bytes()
            if adx[4:8] != header[4:8]:
                log("  параметры кодирования отличаются — беру из нового ADX")
                header = header[:4] + adx[4:8] + header[8:]
            if int.from_bytes(adx[8:12], "big") != sr:
                raise RuntimeError("Частота нового ADX не совпадает")
            new_data = adx[int.from_bytes(adx[2:4], "big") + 4:]
            if len(new_data) < target:
                new_data += b"\x00" * (target - len(new_data))
            new_sfa = header + new_data[:target]
            assert len(new_sfa) == len(sfa)
            out_buf = bytearray(data)
            p = 0
            for s, e in chunks:
                L = e - s
                out_buf[s:e] = new_sfa[p:p + L]
                p += L
            assert p == len(new_sfa)
            changed = sum(1 for i in range(len(data)) if data[i] != out_buf[i])
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            Path(out).write_bytes(out_buf)
            log("  Записано %d байт в %d пакетов; изменено байт всего: %d"
                % (p, len(chunks), changed))
            log("  Готово: %s (%d -> %d байт)" % (out, len(data), len(out_buf)))
            return True
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _audio_map(audio_dir, recursive=True):
        m = {}
        it = os.walk(audio_dir) if recursive else [(audio_dir, [], os.listdir(audio_dir))]
        for dp, _, fs in it:
            for fn in fs:
                if fn.lower().endswith(SfdTool.AUDIO_IN):
                    m.setdefault(os.path.splitext(fn)[0].lower(), os.path.join(dp, fn))
        return m

    @staticmethod
    def _sfd_list(src_dir, recursive=True):
        out = []
        it = os.walk(src_dir) if recursive else [(src_dir, [], os.listdir(src_dir))]
        for dp, _, fs in it:
            for fn in sorted(fs):
                if fn.lower().endswith(".sfd"):
                    out.append(os.path.join(dp, fn))
        return sorted(out)

    @staticmethod
    def batch_replace(src_dir, audio_dir, out_dir, log, ffmpeg, radx, ffprobe,
                      recursive=True, skip_existing=False, stop=None):
        files = SfdTool._sfd_list(src_dir, recursive)
        amap = SfdTool._audio_map(audio_dir, recursive)
        log("SFD: %d, аудиофайлов: %d" % (len(files), len(amap)))
        ok = skip = err = 0
        for path in files:
            if stop is not None and stop.is_set():
                log("Остановлено пользователем")
                break
            rel = os.path.relpath(path, src_dir)
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            out = os.path.join(out_dir, rel)
            log("--- %s ---" % rel)
            audio = amap.get(stem)
            if audio is None:
                log("  нет аудио — пропуск")
                skip += 1
                continue
            if skip_existing and os.path.isfile(out):
                log("  уже готов — пропуск")
                skip += 1
                continue
            try:
                SfdTool.replace(path, audio, out, log, ffmpeg, radx, ffprobe)
                ok += 1
            except Exception as e:
                log("  [!!] %s" % e)
                err += 1
        log("")
        log("Пакетная обработка: OK %d, пропуск %d, ошибок %d" % (ok, skip, err))
        log("Папка вывода: %s" % out_dir)
        return ok, skip, err

    @staticmethod
    def batch_extract(src_dir, out_dir, log, recursive=True, skip_existing=False, stop=None):
        files = SfdTool._sfd_list(src_dir, recursive)
        log("SFD: %d" % len(files))
        ok = skip = err = 0
        for path in files:
            if stop is not None and stop.is_set():
                log("Остановлено пользователем")
                break
            rel = os.path.relpath(path, src_dir)
            out = os.path.join(out_dir, os.path.splitext(rel)[0] + ".adx")
            if skip_existing and os.path.isfile(out):
                skip += 1
                continue
            try:
                SfdTool.extract(path, out, log)
                ok += 1
            except Exception as e:
                log("  [!!] %s: %s" % (rel, e))
                err += 1
        log("Извлечено: %d, пропуск %d, ошибок %d" % (ok, skip, err))
        return ok, skip, err


class SfdTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.ffmpeg = tk.StringVar(value=DEF_FFMPEG)
        self.ffprobe = tk.StringVar(value=DEF_FFPROBE)
        self.radx = tk.StringVar(value=DEF_RADX)
        self.src = tk.StringVar()
        self.audio = tk.StringVar()
        self.out = tk.StringVar()
        self.b_src = tk.StringVar()
        self.b_audio = tk.StringVar()
        self.b_out = tk.StringVar()
        self.recursive = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=False)
        self._q = queue.Queue()
        self._busy = False
        self._stop = threading.Event()
        self._btns = []
        self._ui()
        self.frame.after(100, self._pump)

    def _ui(self):
        t = ttk.LabelFrame(self.frame, text="Инструменты", padding=6)
        t.pack(fill="x", padx=6, pady=5)
        for i, (lbl, var) in enumerate((("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe),
                                        ("radx_encode.exe", self.radx))):
            ttk.Label(t, text=lbl, width=16).grid(row=i, column=0, sticky="w")
            ttk.Entry(t, textvariable=var).grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Button(t, text="...", width=4,
                       command=lambda v=var: self._pick_exe(v)).grid(row=i, column=2)
        t.columnconfigure(1, weight=1)

        s = ttk.LabelFrame(self.frame, text="Одиночный файл", padding=6)
        s.pack(fill="x", padx=6, pady=5)
        rows = (("Исходный SFD:", self.src, self._pick_src),
                ("Новое аудио:", self.audio, self._pick_audio),
                ("Выходной SFD:", self.out, self._pick_out))
        for i, (lbl, var, cmd) in enumerate(rows):
            ttk.Label(s, text=lbl, width=16).grid(row=i, column=0, sticky="w")
            ttk.Entry(s, textvariable=var).grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Button(s, text="...", width=4, command=cmd).grid(row=i, column=2)
        s.columnconfigure(1, weight=1)
        b = ttk.Frame(s)
        b.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._btn(b, "Заменить аудио", self.do_replace).pack(side="left")
        self._btn(b, "Инфо о SFD", self.do_info).pack(side="left", padx=6)
        self._btn(b, "Извлечь аудио (ADX)", self.do_extract).pack(side="left")

        g = ttk.LabelFrame(self.frame, text="Пакетная обработка видео", padding=6)
        g.pack(fill="x", padx=6, pady=5)
        rows = (("Папка с SFD:", self.b_src, lambda: self._pick_dir(self.b_src)),
                ("Папка с аудио:", self.b_audio, lambda: self._pick_dir(self.b_audio)),
                ("Папка вывода:", self.b_out, lambda: self._pick_dir(self.b_out)))
        for i, (lbl, var, cmd) in enumerate(rows):
            ttk.Label(g, text=lbl, width=16).grid(row=i, column=0, sticky="w")
            ttk.Entry(g, textvariable=var).grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Button(g, text="...", width=4, command=cmd).grid(row=i, column=2)
        g.columnconfigure(1, weight=1)
        o = ttk.Frame(g)
        o.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(o, text="Обрабатывать подпапки", variable=self.recursive).pack(side="left")
        ttk.Checkbutton(o, text="Пропускать уже готовые",
                        variable=self.skip_existing).pack(side="left", padx=10)
        b2 = ttk.Frame(g)
        b2.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._btn(b2, "Пакетная замена аудио", self.do_batch).pack(side="left")
        self._btn(b2, "Пакетное извлечение аудио", self.do_batch_extract).pack(side="left", padx=6)
        self.stop_btn = ttk.Button(b2, text="Стоп", command=self._stop.set, state="disabled")
        self.stop_btn.pack(side="left")

        lf = ttk.LabelFrame(self.frame, text="Лог", padding=4)
        lf.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.logw = scrolledtext.ScrolledText(lf, wrap="none", height=12, font=("Consolas", 9))
        self.logw.pack(fill="both", expand=True)
        bar = ttk.Frame(lf)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="Очистить", command=lambda: self.logw.delete("1.0", "end")).pack(side="left")
        ttk.Button(bar, text="Сохранить лог…", command=self.save_log).pack(side="left", padx=6)
        ttk.Label(bar, text="Аудио ищется по имени файла (movie.sfd <- movie.wav)",
                  foreground="gray").pack(side="left", padx=8)

    def _btn(self, parent, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        self._btns.append(b)
        return b

    def _pick_exe(self, var):
        p = filedialog.askopenfilename(parent=self.frame, title="Инструменты",
                                       filetypes=[("EXE", "*.exe"), ("All", "*.*")])
        if p:
            var.set(p)

    def _pick_dir(self, var):
        p = filedialog.askdirectory(parent=self.frame)
        if p:
            var.set(p)

    def _pick_src(self):
        p = filedialog.askopenfilename(parent=self.frame, title="Исходный SFD:",
                                       filetypes=[("SFD", "*.sfd"), ("All", "*.*")])
        if p:
            self.src.set(p)
            if not self.out.get():
                self.out.set(os.path.splitext(p)[0] + "_new.sfd")

    def _pick_audio(self):
        p = filedialog.askopenfilename(parent=self.frame, title="Новое аудио:",
                                       filetypes=[("Audio", "*.wav *.mp3 *.flac *.adx *.ogg *.m4a"),
                                                  ("All", "*.*")])
        if p:
            self.audio.set(p)

    def _pick_out(self):
        p = filedialog.asksaveasfilename(parent=self.frame, title="Выходной SFD:",
                                         defaultextension=".sfd",
                                         filetypes=[("SFD", "*.sfd"), ("All", "*.*")])
        if p:
            self.out.set(p)

    def save_log(self):
        p = filedialog.asksaveasfilename(parent=self.frame, title="Сохранить лог",
                                         initialfile="sfd_log.txt", defaultextension=".txt",
                                         filetypes=[("TXT", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.logw.get("1.0", "end-1c"))

    def log(self, m):
        self._q.put(("log", str(m)))

    def _pump(self):
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "log":
                    self.logw.insert("end", tr_text(val) + "\n")
                    self.logw.see("end")
                elif kind == "done":
                    self._busy = False
                    for b in self._btns:
                        b.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
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
        self._stop.clear()
        for b in self._btns:
            b.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def worker():
            try:
                fn()
            except Exception as e:
                self.log("[!!] %s" % e)
            finally:
                self._q.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def do_info(self):
        src = self.src.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("Ошибка", "Исходный SFD:", parent=self.frame)
            return
        fp = self.ffprobe.get().strip()
        self.logw.delete("1.0", "end")
        self._start(lambda: SfdTool.info(src, self.log, fp))

    def do_replace(self):
        src, aud, out = self.src.get().strip(), self.audio.get().strip(), self.out.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("Ошибка", "Исходный SFD:", parent=self.frame)
            return
        if not aud or not os.path.isfile(aud):
            messagebox.showerror("Ошибка", "Новое аудио:", parent=self.frame)
            return
        if not out:
            out = os.path.splitext(src)[0] + "_new.sfd"
            self.out.set(out)
        if os.path.abspath(out) == os.path.abspath(src):
            messagebox.showerror("Ошибка", "Нельзя писать поверх оригинала", parent=self.frame)
            return
        ff, rx, fp = self.ffmpeg.get().strip(), self.radx.get().strip(), self.ffprobe.get().strip()
        self.logw.delete("1.0", "end")
        self.log("=== Заменить аудио в SFD ===")
        self.log(src)
        self._start(lambda: SfdTool.replace(src, aud, out, self.log, ff, rx, fp))

    def do_extract(self):
        src = self.src.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showerror("Ошибка", "Исходный SFD:", parent=self.frame)
            return
        out = filedialog.asksaveasfilename(parent=self.frame, title="Извлечь аудио (ADX)",
                                           initialfile=os.path.splitext(os.path.basename(src))[0] + ".adx",
                                           defaultextension=".adx",
                                           filetypes=[("ADX", "*.adx"), ("All", "*.*")])
        if not out:
            return
        self.logw.delete("1.0", "end")
        self._start(lambda: SfdTool.extract(src, out, self.log))

    def do_batch(self):
        src, aud, out = self.b_src.get().strip(), self.b_audio.get().strip(), self.b_out.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Папка с SFD:", parent=self.frame)
            return
        if not aud or not os.path.isdir(aud):
            messagebox.showerror("Ошибка", "Папка с аудио:", parent=self.frame)
            return
        if not out:
            messagebox.showerror("Ошибка", "Папка вывода:", parent=self.frame)
            return
        os.makedirs(out, exist_ok=True)
        ff, rx, fp = self.ffmpeg.get().strip(), self.radx.get().strip(), self.ffprobe.get().strip()
        rec, sk = self.recursive.get(), self.skip_existing.get()
        self.logw.delete("1.0", "end")
        self.log("=== Пакетная замена аудио ===")
        self._start(lambda: SfdTool.batch_replace(src, aud, out, self.log, ff, rx, fp,
                                                  rec, sk, self._stop))

    def do_batch_extract(self):
        src, out = self.b_src.get().strip(), self.b_out.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Ошибка", "Папка с SFD:", parent=self.frame)
            return
        if not out:
            messagebox.showerror("Ошибка", "Папка вывода:", parent=self.frame)
            return
        os.makedirs(out, exist_ok=True)
        rec, sk = self.recursive.get(), self.skip_existing.get()
        self.logw.delete("1.0", "end")
        self.log("=== Пакетное извлечение аудио ===")
        self._start(lambda: SfdTool.batch_extract(src, out, self.log, rec, sk, self._stop))


# ============================================================================
# =========================  УСТАНОВКА В TOOL  ===============================
# ============================================================================
def _find_exe_subnotebook(app):
    """Ищем вкладку EXE и убираем старую TSV-вкладку спрайтов."""
    nb = app.notebook
    try:
        n = nb.index("end")
    except Exception:
        return None
    for i in range(n):
        if "EXE" in nb.tab(i, "text").upper():
            frame = nb.nametowidget(nb.tabs()[i])
            sub = None
            for ch in frame.winfo_children():
                if isinstance(ch, ttk.Notebook):
                    sub = ch
                    break
            if sub is None:
                return None
            for j in range(sub.index("end") - 1, -1, -1):
                t = sub.tab(j, "text").lower()
                if "спрайт" in t or "sprite" in t or "tsv" in t:
                    w = sub.nametowidget(sub.tabs()[j])
                    sub.forget(j)
                    try:
                        w.destroy()
                    except Exception:
                        pass
            return sub
    return None


class UltimateGUIPlus(Tool.UltimateGUI):
    def __init__(self, root):
        super().__init__(root)
        self._install_plus()

    def _install_plus(self):
        root = self.root
        bar = ttk.Frame(root)
        try:
            bar.pack(fill="x", padx=10, pady=(8, 0), before=self.notebook)
        except Exception:
            bar.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(bar, text="Язык / Language:").pack(side="left")
        cb = ttk.Combobox(bar, values=["Русский", "English"], state="readonly", width=12)
        cb.set("English" if LANG == "en" else "Русский")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: set_lang(cb.get(), root))
        ttk.Label(bar, text="(интерфейс переводится сразу)").pack(side="left", padx=8)

        sub = None
        try:
            sub = _find_exe_subnotebook(self)
        except Exception:
            sub = None
        self.sprite_tab = SpriteEditorTab(sub if sub is not None else self.notebook)
        if sub is not None:
            sub.add(self.sprite_tab.frame, text="Спрайты EXE (по имени)")
        else:
            self.notebook.add(self.sprite_tab.frame, text="EXE (спрайты)")

        self.sfd_tab = SfdTab(self.notebook)
        self.notebook.add(self.sfd_tab.frame, text="SFD (видео)")

        try:
            orig_log = self.log

            def log_plus(msg):
                orig_log(tr_text(str(msg)))

            self.log = log_plus
        except Exception:
            pass

        translate_tree(root)
        _tick(root)


def main():
    load_cfg()
    _patch_dialogs()
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app = UltimateGUIPlus(root)
    try:
        root.title("TMNT 2003 Ultimate Modding Tool + ToolPlus")
    except Exception:
        pass
    root.geometry("1360x980")
    translate_tree(root)
    root.mainloop()


if __name__ == "__main__":
    main()