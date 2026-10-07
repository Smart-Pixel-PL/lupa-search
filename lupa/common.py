"""Shared config, database and file-kind helpers."""
import os
import sqlite3
import tomllib
import unicodedata
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "data"
THUMBS = DATA / "thumbs"
DB_PATH = DATA / "lupa.db"
DATA.mkdir(exist_ok=True)
THUMBS.mkdir(exist_ok=True)


def nfc(s: str) -> str:
    """macOS stores names decomposed (NFD); the tokenizer handles composed Polish letters far better."""
    return unicodedata.normalize("NFC", s) if s else s


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p)) if p else p


def load_config():
    path = PROJECT / "lupa.toml"
    if not path.exists():  # first run: personal copy of the example config
        path.write_text((PROJECT / "lupa.example.toml").read_text())
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["roots"] = [_expand(r) for r in cfg.get("roots", ["~"])]
    cfg["exclude_paths"] = [_expand(p) for p in cfg.get("exclude_paths", [])] + [str(PROJECT)]
    cfg["include_paths"] = [_expand(p) for p in cfg.get("include_paths", [])]
    cfg["places"] = [{**pl, "path": _expand(pl["path"])} for pl in cfg.get("places", [])]
    cfg["exclude_dirs"] = set(cfg.get("exclude_dirs", []))
    cfg["package_exts"] = tuple(e.lower() for e in cfg.get("package_exts", []))
    cfg["skip_exts"] = set(e.lower() for e in cfg.get("skip_exts", []))
    return cfg


CFG = load_config()
DIM = CFG.get("dim", 256)

# vector types
V_NAME, V_TEXT, V_VISUAL, V_AUDIO = 0, 1, 2, 3

KINDS = {
    "image": "jpg jpeg png webp gif bmp tif tiff heic heif avif svg ico",
    "raw": "cr2 cr3 nef arw dng raf orf rw2",
    "video": "mp4 mov m4v avi mkv webm wmv flv mpg mpeg mts m2ts 3gp",
    "audio": "mp3 wav m4a aac flac ogg opus aiff aif wma caf",
    "pdf": "pdf",
    "doc": "doc docx odt rtf pages",
    "sheet": "xls xlsx xlsm ods csv numbers tsv",
    "slides": "ppt pptx odp key",
    "design": "psd psb ai eps indd sketch fig afdesign afphoto afpub xd aep prproj drp cdr",
    "text": "txt md markdown rst log tex org",
    "web": "html htm xml",
    "code": "py js ts tsx jsx json yaml yml toml css scss sh zsh swift kt java c h cpp m rb go rs php sql ini cfg vue svelte lua r ipynb",
    "font": "ttf otf woff woff2",
    "archive": "zip rar 7z tar gz tgz bz2 xz dmg iso pkg",
    "model3d": "blend obj fbx glb gltf stl usdz c4d 3ds",
}
EXT_KIND = {"." + e: k for k, exts in KINDS.items() for e in exts.split()}

KIND_LABELS = {
    "image": "Obrazy", "raw": "Zdjęcia RAW", "video": "Wideo", "audio": "Audio", "pdf": "PDF",
    "doc": "Dokumenty", "sheet": "Arkusze", "slides": "Prezentacje", "design": "Projekty graficzne",
    "text": "Tekst", "web": "HTML", "code": "Kod", "font": "Fonty", "archive": "Archiwa",
    "model3d": "3D", "package": "Aplikacje/pakiety", "other": "Inne",
}

# Indexing priority for content (lower = earlier)
KIND_PRIORITY = {"image": 0, "pdf": 1, "doc": 1, "slides": 2, "sheet": 2, "design": 2, "raw": 3,
                 "video": 4, "text": 5, "web": 6, "audio": 7, "code": 8}


def kind_of(path: str, is_dir=False) -> str:
    ext = os.path.splitext(path)[1].lower()
    if is_dir:
        return "package"
    return EXT_KIND.get(ext, "other")


def root_of(path: str) -> str:
    for r in sorted(CFG["roots"], key=len, reverse=True):
        if path == r or path.startswith(r.rstrip("/") + "/"):
            return r
    return ""


def root_online(root: str) -> bool:
    try:
        return os.path.isdir(root) and any(True for _ in os.scandir(root))
    except OSError:
        return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, root TEXT, name TEXT, ext TEXT, kind TEXT,
  size INTEGER, mtime REAL, ctime REAL, scan INTEGER DEFAULT 0,
  stage INTEGER DEFAULT 0, error TEXT, missing INTEGER DEFAULT 0, dataless INTEGER DEFAULT 0,
  width INTEGER, height INTEGER, duration REAL, pages INTEGER, snippet TEXT, thumb INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_files_stage ON files(stage);
CREATE INDEX IF NOT EXISTS ix_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS ix_files_kind ON files(kind);
CREATE TABLE IF NOT EXISTS vectors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER NOT NULL, vtype INTEGER NOT NULL,
  part INTEGER DEFAULT 0, vec BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS ix_vec_file ON vectors(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(name, folder, body, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, color TEXT);
CREATE TABLE IF NOT EXISTS file_tags(file_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(file_id, tag_id));
CREATE INDEX IF NOT EXISTS ix_ft_tag ON file_tags(tag_id);
CREATE TABLE IF NOT EXISTS collections(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, query TEXT, kinds TEXT, icon TEXT, pos INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

DEFAULT_COLLECTIONS = [
    ("Faktury i rachunki", "faktura VAT rachunek paragon kwota do zapłaty", "pdf,doc,image,sheet", "🧾"),
    ("Umowy", "umowa zawarta pomiędzy stronami, warunki, podpis", "pdf,doc", "✍️"),
    ("Oferty", "oferta handlowa cennik wycena dla klienta", "pdf,doc,slides,sheet", "💼"),
    ("Logotypy", "logo firmy, znak graficzny na przezroczystym tle", "image,design,pdf", "🔷"),
    ("Zrzuty ekranu", "zrzut ekranu interfejsu aplikacji lub strony internetowej", "image", "🖥️"),
    ("Zdjęcia ludzi", "zdjęcie przedstawiające ludzi, portret, grupa osób", "image,raw,video", "🧑‍🤝‍🧑"),
    ("Dokumenty tożsamości", "skan dowodu osobistego, paszportu lub prawa jazdy", "image,pdf", "🪪"),
    ("Krajobrazy", "zdjęcie krajobrazu, natura, góry, morze, zachód słońca", "image,raw", "🏞️"),
    ("Plakaty i grafiki", "plakat reklamowy, baner, grafika promocyjna, ulotka", "image,design,pdf", "🎨"),
    ("Nagrania głosu", "nagranie mowy, ktoś mówi, wywiad, notatka głosowa", "audio,video", "🎙️"),
]


def connect(readonly=False) -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    if not readonly:
        con.executescript(SCHEMA)
        if con.execute("SELECT count(*) FROM collections").fetchone()[0] == 0:
            con.executemany("INSERT INTO collections(name,query,kinds,icon,pos) VALUES(?,?,?,?,?)",
                            [(*c, i) for i, c in enumerate(DEFAULT_COLLECTIONS)])
        if con.execute("SELECT count(*) FROM tags").fetchone()[0] == 0:
            con.execute("INSERT INTO tags(name,color) VALUES('Ulubione','#f5b301')")
        con.commit()
    return con


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def thumb_path(fid: int) -> Path:
    d = THUMBS / str(fid // 1000)
    d.mkdir(exist_ok=True)
    return d / f"{fid}.webp"
