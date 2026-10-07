"""Porządki — find reclaimable space on the local (system) disk.

Layers, from most to least certain:
  1. hard guards  — system locations are never touched; only the user's folders and /Applications
  2. known-safe categories — caches, logs, developer caches, installers (apps rebuild caches themselves)
  3. leftovers of uninstalled apps — ~/Library folders matched against bundle ids of installed apps
  4. real usage — macOS "last opened" dates for apps, downloads and big files
Nothing is deleted here: items go to the Trash through Finder (so "Put Back" works), and only
paths that appeared in the latest scan can be trashed at all.
"""
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .common import CFG, DATA, PROJECT, connect, meta_get, meta_set

HOME = os.path.expanduser("~")
LIB = os.path.join(HOME, "Library")
REPORT = DATA / "cleanup.json"
LOG = DATA / "cleanup_log.jsonl"
CC = {"every_days": 7, "low_space_percent": 15, "big_file_mb": 200, "old_days": 365,
      "downloads_old_days": 90, "unused_app_days": 180, "leftover_min_mb": 20, **CFG.get("cleanup", {})}

# Never offered, never trashed (even if something matches by accident).
PROTECTED = [
    str(PROJECT), os.path.join(HOME, ".cache", "huggingface"), "/Applications/Lupa.app",
    os.path.join(LIB, "Mobile Documents"), os.path.join(LIB, "Keychains"), os.path.join(LIB, "Mail"),
    os.path.join(LIB, "Messages"), os.path.join(LIB, "Calendars"), os.path.join(LIB, "Photos"),
    os.path.join(LIB, "CloudStorage"), os.path.join(LIB, "Application Support", "AddressBook"),
    os.path.join(LIB, "Application Support", "MobileSync"),  # offered only via its own category
]
NEVER_EXACT = {"/", HOME, LIB, "/Applications", "/Users", os.path.join(HOME, "Downloads"),
               os.path.join(HOME, "Desktop"), os.path.join(HOME, "Documents"), os.path.join(HOME, ".cache"),
               os.path.join(HOME, ".Trash")}

# Apple / system folders in ~/Library that do not look like "com.apple.*".
APPLE_NAMES = {
    "accounts", "addressbook", "animoji", "appstore", "assistants", "callhistorydb", "callhistorytransactions",
    "cloudkit", "crashreporter", "dock", "familycircle", "fileprovider", "icdd", "identityservices",
    "knowledge", "mobilesync", "networkserviceproxy", "quicklook", "syncservices", "icloud", "homeenergyd",
    "caches", "contacts", "dmd", "facetime", "findmy", "gamekit", "locationaccessstored", "siri",
    "spotlight", "sharedimagecache", "screentime", "stocks", "tipsd", "translation", "weather", "wallpaper",
    "videoconference", "audio", "coreparsec", "geoservices", "homekit", "metadata", "passkit", "safari",
    "shortcuts", "ubiquity", "usernotifications", "voicetrigger", "ckpersistence", "chromium",
}

_state = {"running": False, "progress": "", "lock": threading.Lock()}


# ------------------------------------------------------------------ helpers
def _du(path: str) -> int:
    try:
        out = subprocess.run(["du", "-skx", path], capture_output=True, text=True, timeout=600).stdout
        return int(out.split()[0]) * 1024 if out else 0
    except Exception:
        return 0


def _sizes(paths):
    with ThreadPoolExecutor(6) as pool:
        return dict(zip(paths, pool.map(_du, paths)))


def _newest_mtime(path: str) -> float:
    """Most recent change of the folder or anything directly inside it (cheap 'is it still in use?' signal)."""
    try:
        m = os.stat(path).st_mtime
        with os.scandir(path) as it:
            for i, e in enumerate(it):
                if i > 400:
                    break
                try:
                    m = max(m, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
        return m
    except OSError:
        return 0


def _last_used(paths):
    """kMDItemLastUsedDate for many paths at once (Spotlight)."""
    out = {}
    for i in range(0, len(paths), 100):
        part = paths[i:i + 100]
        try:
            raw = subprocess.run(["mdls", "-name", "kMDItemLastUsedDate", "-raw", *part],
                                 capture_output=True, timeout=60).stdout.split(b"\0")
        except Exception:
            continue
        for p, v in zip(part, raw):
            v = v.decode(errors="ignore").strip()
            try:
                out[p] = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                out[p] = None
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def installed_apps():
    """{bundle_id: app_path} plus normalised names of every installed app (incl. helpers/system)."""
    ids, names = {}, set()
    roots = ["/Applications", "/Applications/Utilities", os.path.join(HOME, "Applications"),
             "/System/Applications", "/System/Applications/Utilities", "/System/Library/CoreServices",
             "/Library/Application Support"]
    seen = set()

    def visit(app, depth=0):
        if app in seen:
            return
        seen.add(app)
        try:
            with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
                info = plistlib.load(f)
            bid = (info.get("CFBundleIdentifier") or "").lower()
            if bid:
                ids.setdefault(bid, app)
            for k in ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable"):
                if info.get(k):
                    names.add(_norm(info[k]))
        except Exception:
            pass
        names.add(_norm(os.path.basename(app)[:-4]))
        if depth < 2:  # helper apps / login items inside the bundle
            for sub in ("Contents/Library/LoginItems", "Contents/Helpers", "Contents/Frameworks", "Contents/MacOS"):
                d = os.path.join(app, sub)
                if os.path.isdir(d):
                    for n in os.listdir(d):
                        if n.endswith(".app"):
                            visit(os.path.join(d, n), depth + 1)

    for r in roots:
        try:
            for n in os.listdir(r):
                p = os.path.join(r, n)
                if n.endswith(".app"):
                    visit(p)
                elif os.path.isdir(p) and r == "/Applications":
                    for m in os.listdir(p):  # e.g. /Applications/Adobe Photoshop 2026/*.app
                        if m.endswith(".app"):
                            visit(os.path.join(p, m))
        except OSError:
            pass
    names.discard("")
    return ids, names


def _vendor(bid: str) -> str:
    parts = bid.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else bid


def _match_app(name: str, ids: dict, names: set):
    """Return (installed?, app_path or None, confidence) for a ~/Library folder name."""
    low = name.lower().removesuffix(".savedstate")
    if low.startswith(("com.apple.", "group.com.apple.", "apple")) or ".apple." in low:
        return True, None, "system"
    if _norm(low) in APPLE_NAMES:
        return True, None, "system"
    # Group Containers: "TEAMID.vendor.product" or "group.com.vendor.x"
    m = re.match(r"^(?:[A-Z0-9]{10}\.)(.+)$", name)
    if m:
        low = m.group(1).lower()
    low = low.removeprefix("group.")
    if re.match(r"^(com|org|net|io|pl|ai|app|dev|co|me|us|de|uk|so|tv|is|cc|md|it|fm|sh|xyz)\.[\w-]+", low):
        if low in ids:
            return True, ids[low], "high"
        for bid, path in ids.items():
            if bid.startswith(low) or low.startswith(bid):
                return True, path, "high"
        vend = _vendor(low)
        if any(_vendor(b) == vend for b in ids):
            return True, None, "high"  # same vendor still installed (shared data) — keep
        return False, None, "high"
    n = _norm(low)
    if len(n) < 3:
        return True, None, "system"
    for an in names:
        if len(an) >= 4 and (an in n or n in an):
            for bid, path in ids.items():
                if _norm(os.path.basename(path)[:-4]) == an:
                    return True, path, "medium"
            return True, None, "medium"
    return False, None, "medium"


IOS_BACKUPS = os.path.join(LIB, "Application Support", "MobileSync", "Backup") + "/"


def _guard(path: str, category: str = "") -> str | None:
    """Reason why this path must never be trashed, or None if it is allowed."""
    p = os.path.abspath(path)
    if category == "ios_backups":  # the only way into MobileSync: one device backup folder at a time
        return None if p.startswith(IOS_BACKUPS) and "/" not in p[len(IOS_BACKUPS):] and os.path.isdir(p) \
            else "to nie jest folder kopii zapasowej urządzenia"
    if p in NEVER_EXACT or p.rstrip("/") in NEVER_EXACT:
        return "chroniona lokalizacja"
    if p.startswith("/Volumes/"):
        return "Porządki obejmują tylko dysk lokalny"
    if not (p.startswith("/Users/") or p.startswith("/Applications/")):
        return "poza folderami użytkownika"
    for pr in PROTECTED + _user_protected():
        if p == pr or p.startswith(pr.rstrip("/") + "/"):
            return "na liście chronionych"
    if os.path.dirname(p) == LIB:  # ~/Library/<top-level> itself
        return "folder systemowy biblioteki"
    if not os.path.lexists(p):
        return "nie istnieje"
    return None


def _user_protected():
    try:
        return json.loads(meta_get(connect(readonly=True), "cleanup_protect", "[]") or "[]")
    except Exception:
        return []


def _item(path, size, kind, **kw):
    return {"path": path, "name": os.path.basename(path.rstrip("/")) or path, "size": size, "kind": kind, **kw}


def _running_bundle_ids():
    try:
        out = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=10).stdout
        return {m.lower() for m in re.findall(r'bundleID="([^"]+)"', out)}
    except Exception:
        return set()


def _app_for(name: str, ids: dict):
    low = name.lower().removesuffix(".shipit")
    if low in ids:
        return ids[low]
    for bid, path in ids.items():
        if low.startswith(bid) or bid.startswith(low):
            return path
    n = _norm(low)
    first = _norm(re.split(r"(?<=[a-z])(?=[A-Z])|[\s._-]", name)[0])  # "BraveSoftware" -> "brave"
    best = None
    for bid, path in ids.items():
        if not path.startswith(("/Applications/", os.path.join(HOME, "Applications"))):
            continue
        an = _norm(os.path.basename(path)[:-4])
        if an == n or n == _norm(bid.split(".")[-1]):
            return path
        if (len(an) >= 4 and an in n) or (len(n) >= 5 and an.startswith(n)) or (len(first) >= 4 and an.startswith(first)):
            best = best or path
    return best


# ------------------------------------------------------------------ categories
def cat_caches(ids, running):
    base = os.path.join(LIB, "Caches")
    items = []
    try:
        names = [n for n in os.listdir(base) if not n.startswith(".")]
    except OSError:
        return items
    paths = [os.path.join(base, n) for n in names if not n.lower().startswith("com.apple.")]
    sizes = _sizes(paths)
    for p in paths:
        s = sizes.get(p, 0)
        if s < 20 * 1024 ** 2:
            continue
        app = _app_for(os.path.basename(p), ids)
        bid = next((b for b, a in ids.items() if a == app), "")
        note = "Aplikacja jest teraz otwarta — najlepiej ją zamknąć przed czyszczeniem." if bid in running else ""
        items.append(_item(p, s, "folder", app=app, note=note, preselect=not note))
    return items


def cat_dev():
    cands = [
        (os.path.join(LIB, "Developer", "Xcode", "DerivedData"), "Pliki pośrednie kompilacji Xcode"),
        (os.path.join(LIB, "Developer", "Xcode", "iOS DeviceSupport"), "Symbole starszych wersji iOS"),
        (os.path.join(LIB, "Developer", "CoreSimulator", "Caches"), "Cache symulatorów iOS"),
        (os.path.join(HOME, ".npm", "_cacache"), "Cache npm"),
        (os.path.join(HOME, ".cache", "uv"), "Cache uv (Python)"),
        (os.path.join(HOME, ".cache", "pip"), "Cache pip"),
        (os.path.join(HOME, ".cache", "yarn"), "Cache yarn"),
        (os.path.join(HOME, ".gradle", "caches"), "Cache Gradle"),
        (os.path.join(HOME, ".cocoapods", "repos"), "Repozytoria CocoaPods"),
        (os.path.join(HOME, "Library", "pnpm", "store"), "Magazyn pnpm"),
    ]
    paths = [p for p, _ in cands if os.path.isdir(p)]
    sizes = _sizes(paths)
    return [_item(p, sizes[p], "folder", note=d, preselect=True) for p, d in cands
            if p in sizes and sizes[p] > 50 * 1024 ** 2]


def cat_logs():
    base = os.path.join(LIB, "Logs")
    try:
        paths = [os.path.join(base, n) for n in os.listdir(base) if not n.startswith(".")]
    except OSError:
        return []
    sizes = _sizes(paths)
    return [_item(p, s, "folder" if os.path.isdir(p) else "file", preselect=True)
            for p, s in sizes.items() if s > 5 * 1024 ** 2]


def cat_leftovers(ids, names):
    bases = ["Application Support", "Containers", "Group Containers", "HTTPStorages", "WebKit"]
    cands = []
    for b in bases:
        d = os.path.join(LIB, b)
        try:
            for n in os.listdir(d):
                if n.startswith("."):
                    continue
                p = os.path.join(d, n)
                if _guard(p):
                    continue
                key = n
                if b == "Containers":  # real bundle id lives in the container metadata
                    try:
                        with open(os.path.join(p, ".com.apple.containermanager.metadata.plist"), "rb") as f:
                            key = plistlib.load(f).get("MCMMetadataIdentifier", n)
                    except Exception:
                        pass
                inst, _, conf = _match_app(key, ids, names)
                if not inst:
                    cands.append((p, conf, b))
        except OSError:
            pass
    sizes = _sizes([c[0] for c in cands])
    min_b = CC["leftover_min_mb"] * 1024 ** 2
    now, items = time.time(), []
    for p, conf, b in cands:
        s = sizes.get(p, 0)
        if s < min_b:
            continue
        last = _newest_mtime(p)
        if now - last < 30 * 86400:  # still being written to — some process uses it
            continue
        items.append(_item(p, s, "folder", mtime=last, confidence=conf, note=f"~/Library/{b}",
                           preselect=False))
    return items


def cat_unused_apps(ids):
    apps = sorted({a for a in ids.values() if a.startswith("/Applications/") and a.count(".app") == 1})
    used = _last_used(apps)
    limit = time.time() - CC["unused_app_days"] * 86400
    old = []
    for a in apps:
        try:
            with open(os.path.join(a, "Contents", "Info.plist"), "rb") as f:
                bid = (plistlib.load(f).get("CFBundleIdentifier") or "").lower()
        except Exception:
            bid = ""
        if bid.startswith("com.apple.") or _guard(a):
            continue
        lu = used.get(a)
        added = os.stat(a).st_mtime
        if (lu and lu < limit) or (not lu and added < limit):
            old.append((a, lu))
    sizes = _sizes([a for a, _ in old])
    return [_item(a, sizes[a], "app", app=a, last_used=lu, preselect=False,
                  note="macOS nie ma danych o ostatnim użyciu" if not lu else "") for a, lu in old if sizes[a] > 50 * 1024 ** 2]


def _local_index_rows(sql, args=()):
    c = connect(readonly=True)
    return c.execute(sql, args).fetchall()


def cat_installers():
    rows = _local_index_rows("""SELECT id, path, size, mtime FROM files WHERE root NOT LIKE '/Volumes/%'
                                AND ext IN ('.dmg','.pkg','.iso','.mpkg') AND size > 1000000""")
    month = time.time() - 30 * 86400
    return [_item(r["path"], r["size"], "file", id=r["id"], mtime=r["mtime"], preselect=r["mtime"] < month)
            for r in rows if os.path.exists(r["path"]) and not _guard(r["path"])]


def cat_downloads():
    d = os.path.join(HOME, "Downloads")
    try:
        paths = [os.path.join(d, n) for n in os.listdir(d) if not n.startswith(".")]
    except OSError:
        return []
    used = _last_used(paths)
    limit = time.time() - CC["downloads_old_days"] * 86400
    ids = {r["path"]: r["id"] for r in _local_index_rows(
        "SELECT id, path FROM files WHERE path >= ? AND path < ?", (d + "/", d + "0"))}
    out = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        lu = used.get(p) or st.st_mtime
        if lu > limit or p.lower().endswith((".dmg", ".pkg", ".iso")):
            continue  # installers have their own category
        size = _du(p) if os.path.isdir(p) else st.st_size
        out.append(_item(p, size, "folder" if os.path.isdir(p) else "file", id=ids.get(p), mtime=st.st_mtime,
                         last_used=used.get(p), preselect=False))
    return out


def cat_big_old():
    limit = time.time() - CC["old_days"] * 86400
    rows = _local_index_rows("""SELECT id, path, size, mtime, kind FROM files WHERE root NOT LIKE '/Volumes/%'
                                AND size > ? AND mtime < ? AND kind != 'package'
                                AND ext NOT IN ('.dmg','.pkg','.iso','.mpkg')
                                ORDER BY size DESC LIMIT 300""", (CC["big_file_mb"] * 1024 ** 2, limit))
    rows = [r for r in rows if os.path.exists(r["path"]) and not _guard(r["path"])]
    used = _last_used([r["path"] for r in rows])
    return [_item(r["path"], r["size"], "file", id=r["id"], mtime=r["mtime"], last_used=used.get(r["path"]),
                  preselect=False) for r in rows
            if not used.get(r["path"]) or used[r["path"]] < limit]


COPY_MARK = re.compile(r"(\(\d+\)|[ _-](kopia|copy|copia)\b|\bkopia\b)", re.I)


def _hash(path, full=False):
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            if full:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            else:
                h.update(f.read(65536))
                f.seek(max(0, os.path.getsize(path) - 65536))
                h.update(f.read(65536))
    except OSError:
        return None
    return h.hexdigest()


def cat_duplicates():
    rows = _local_index_rows("""SELECT f.id, f.path, f.size, f.mtime, f.ctime FROM files f JOIN
                                (SELECT size FROM files WHERE root NOT LIKE '/Volumes/%' AND size >= 1048576
                                 AND kind != 'package' GROUP BY size HAVING count(*) > 1) d ON d.size = f.size
                                WHERE f.root NOT LIKE '/Volumes/%' AND f.kind != 'package'""")
    rows = [r for r in rows if os.path.exists(r["path"])]
    by = {}
    for r in rows:
        by.setdefault(r["size"], []).append(r)
    groups = []
    with ThreadPoolExecutor(6) as pool:
        for size, rs in by.items():
            quick = list(pool.map(_hash, [r["path"] for r in rs]))
            buckets = {}
            for r, q in zip(rs, quick):
                if q:
                    buckets.setdefault(q, []).append(r)
            for b in buckets.values():
                if len(b) < 2:
                    continue
                full = list(pool.map(lambda r: _hash(r["path"], True), b))
                fb = {}
                for r, h in zip(b, full):
                    if h:
                        fb.setdefault(h, []).append(r)
                groups += [g for g in fb.values() if len(g) > 1]
    dl, desk = os.path.join(HOME, "Downloads") + "/", os.path.join(HOME, "Desktop") + "/"

    def keep_score(r):  # lower = better to keep
        p = r["path"]
        return (p.startswith((dl, desk)), bool(COPY_MARK.search(os.path.basename(p))), p.count("/"), r["ctime"] or 0)

    items = []
    for gi, g in enumerate(sorted(groups, key=lambda g: -g[0]["size"] * (len(g) - 1))[:400]):
        g = sorted(g, key=keep_score)
        for k, r in enumerate(g):
            p = r["path"]
            items.append(_item(p, r["size"], "file", id=r["id"], mtime=r["mtime"], group=gi, keep=k == 0,
                               preselect=k > 0 and (p.startswith((dl, desk)) or bool(COPY_MARK.search(os.path.basename(p)))),
                               note="Zostaw — najlepsza kopia" if k == 0 else ""))
    return items


def cat_ios_backups():
    d = os.path.join(LIB, "Application Support", "MobileSync", "Backup")
    try:
        paths = [os.path.join(d, n) for n in os.listdir(d) if not n.startswith(".")]
    except OSError:
        return []
    sizes = _sizes(paths)
    out = []
    for p in paths:
        name = os.path.basename(p)
        try:
            with open(os.path.join(p, "Info.plist"), "rb") as f:
                info = plistlib.load(f)
            name = f"{info.get('Device Name', name)} · {info.get('Product Type', '')}"
            last = info.get("Last Backup Date")
            mt = last.timestamp() if last else os.stat(p).st_mtime
        except Exception:
            mt = os.stat(p).st_mtime
        out.append({**_item(p, sizes.get(p, 0), "folder", mtime=mt, preselect=False), "name": name})
    return out


CATEGORIES = [
    # id, title, icon, risk, description
    ("caches", "Cache aplikacji", "🧹", "safe",
     "Pliki tymczasowe przeglądarek i aplikacji. Aplikacje odtworzą je same przy następnym uruchomieniu.", cat_caches),
    ("dev", "Cache narzędzi programisty", "🛠️", "safe",
     "Xcode, npm, pip, uv, yarn — pobiorą się ponownie, gdy będą potrzebne.", cat_dev),
    ("logs", "Logi", "📜", "safe", "Dzienniki diagnostyczne aplikacji. Potrzebne tylko przy szukaniu błędów.", cat_logs),
    ("installers", "Instalatory", "📦", "safe",
     "Pliki .dmg i .pkg. Po zainstalowaniu aplikacji nie są już potrzebne.", cat_installers),
    ("leftovers", "Resztki po odinstalowanych aplikacjach", "👻", "check",
     "Dane w ~/Library aplikacji, których już nie ma na Macu, i nieużywane od co najmniej 30 dni.", cat_leftovers),
    ("downloads", "Stare pliki w Pobranych", "⬇️", "check",
     "Nieotwierane od dawna. Sprawdź, czy czegoś nie potrzebujesz, zanim usuniesz.", cat_downloads),
    ("duplicates", "Duplikaty", "👯", "check",
     "Pliki identyczne co do bajtu (sprawdzone sumą kontrolną). Najlepsza kopia jest oznaczona i zostaje.", cat_duplicates),
    ("unused_apps", "Nieużywane aplikacje", "💤", "check",
     "Aplikacje nieotwierane od dawna. Można je w każdej chwili zainstalować ponownie.", cat_unused_apps),
    ("big_old", "Duże, dawno nieużywane pliki", "🐘", "careful",
     "Duże pliki nieotwierane od ponad roku. Rozważ przeniesienie na NAS zamiast usuwania.", cat_big_old),
    ("ios_backups", "Kopie zapasowe iPhone'a / iPada", "📱", "careful",
     "Lokalne kopie urządzeń. Usuń tylko te, których na pewno nie potrzebujesz (np. starych telefonów).",
     cat_ios_backups),
]


# ------------------------------------------------------------------ scan / report
def disk_info():
    u = shutil.disk_usage("/")
    trash = _du(os.path.join(HOME, ".Trash"))
    return {"total": u.total, "used": u.total - u.free, "free": u.free, "trash": trash,
            "free_pct": round(u.free / u.total * 100, 1), "low": u.free / u.total * 100 < CC["low_space_percent"]}


def scan():
    with _state["lock"]:
        if _state["running"]:
            return False
        _state["running"] = True
    try:
        t0 = time.time()
        _state["progress"] = "Sprawdzam zainstalowane aplikacje"
        ids, names = installed_apps()
        running = _running_bundle_ids()
        cats = []
        for cid, title, icon, risk, desc, fn in CATEGORIES:
            _state["progress"] = title
            try:
                if cid == "caches":
                    items = fn(ids, running)
                elif cid == "leftovers":
                    items = fn(ids, names)
                elif cid == "unused_apps":
                    items = fn(ids)
                else:
                    items = fn()
            except Exception as e:  # one broken category must not kill the report
                items = []
                print("cleanup:", cid, e)
            items = [i for i in items if not _guard(i["path"], cid)]
            items.sort(key=lambda i: (i.get("group", 0), -i["size"]) if cid == "duplicates" else -i["size"])
            reclaim = sum(i["size"] for i in items if not i.get("keep"))
            cats.append({"id": cid, "title": title, "icon": icon, "risk": risk, "desc": desc,
                         "items": items, "total": reclaim, "count": len(items)})
        rep = {"scanned_at": time.time(), "took": round(time.time() - t0, 1), "categories": cats}
        REPORT.write_text(json.dumps(rep, ensure_ascii=False))
        return True
    finally:
        _state["running"] = False
        _state["progress"] = ""


def report():
    try:
        rep = json.loads(REPORT.read_text())
    except Exception:
        rep = {"scanned_at": 0, "categories": []}
    c = connect(readonly=True)
    rep.update(disk=disk_info(), running=_state["running"], progress=_state["progress"],
               settings={"auto_caches": meta_get(c, "cleanup_auto_caches", "0") == "1",
                         "every_days": CC["every_days"], "low_space_percent": CC["low_space_percent"],
                         "protected": _user_protected()})
    return rep


def _candidates():
    try:
        rep = json.loads(REPORT.read_text())
    except Exception:
        return {}
    return {i["path"]: (c["id"], i) for c in rep["categories"] for i in c["items"]}


def _as(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def trash(paths, source="user"):
    """Move scan candidates to the Trash via Finder (keeps "Put Back"). Returns per-path result."""
    cands = _candidates()
    ok_paths, results = [], {}
    for p in paths:
        if p not in cands:
            results[p] = "nie ma tego w ostatnim skanie"
            continue
        why = _guard(p, cands[p][0])
        if why:
            results[p] = why
            continue
        ok_paths.append(p)
    for i in range(0, len(ok_paths), 50):
        part = ok_paths[i:i + 50]
        lines = ['set out to ""']
        for p in part:
            lines += ["try", f'tell application "Finder" to delete (POSIX file "{_as(p)}" as alias)',
                      'set out to out & "ok" & linefeed', "on error e",
                      'set out to out & "err:" & e & linefeed', "end try"]
        lines.append("return out")
        try:
            res = subprocess.run(["osascript", "-e", "\n".join(lines)], capture_output=True, text=True,
                                 timeout=600).stdout.splitlines()
        except Exception as e:
            res = [f"err:{e}"] * len(part)
        for p, r in zip(part, res + ["err:brak odpowiedzi Findera"] * len(part)):
            results[p] = "ok" if r == "ok" else r.removeprefix("err:")
    freed = 0
    with open(LOG, "a") as log:
        for p, r in results.items():
            if r == "ok":
                size = cands[p][1]["size"]
                freed += size
                log.write(json.dumps({"t": time.time(), "path": p, "size": size, "category": cands[p][0],
                                      "by": source}, ensure_ascii=False) + "\n")
    # drop trashed items from the cached report
    try:
        rep = json.loads(REPORT.read_text())
        for c in rep["categories"]:
            c["items"] = [i for i in c["items"] if results.get(i["path"]) != "ok"]
            c["count"] = len(c["items"])
            c["total"] = sum(i["size"] for i in c["items"] if not i.get("keep"))
        REPORT.write_text(json.dumps(rep, ensure_ascii=False))
    except Exception:
        pass
    return {"results": results, "freed": freed}


def empty_trash():
    r = subprocess.run(["osascript", "-e", 'tell application "Finder" to empty trash'],
                       capture_output=True, text=True, timeout=1800)
    return {"ok": r.returncode == 0, "error": r.stderr.strip()}


def set_protect(path: str, add: bool):
    c = connect()
    cur = set(json.loads(meta_get(c, "cleanup_protect", "[]") or "[]"))
    (cur.add if add else cur.discard)(path)
    meta_set(c, "cleanup_protect", json.dumps(sorted(cur), ensure_ascii=False))
    c.commit()


def notify(title, msg):
    subprocess.run(["osascript", "-e", f'display notification "{_as(msg)}" with title "{_as(title)}"'],
                   capture_output=True, timeout=10)


def periodic():
    """Called from the server's background loop: weekly scan, low-space alert, optional cache auto-clean."""
    c = connect()
    rep = report()
    if not rep["running"] and time.time() - rep.get("scanned_at", 0) > CC["every_days"] * 86400:
        scan()
        rep = report()
        if meta_get(c, "cleanup_auto_caches", "0") == "1":
            paths = [i["path"] for cat in rep["categories"] if cat["id"] in ("caches", "dev", "logs")
                     for i in cat["items"] if not i.get("note", "").startswith("Aplikacja jest teraz otwarta")]
            res = trash(paths, source="auto")
            if res["freed"]:
                notify("Lupa · Porządki", f"Przeniesiono do Kosza {res['freed'] / 1e9:.1f} GB cache. "
                                          "Opróżnij Kosz, aby odzyskać miejsce.")
    d = rep["disk"]
    if d["low"] and time.time() - float(meta_get(c, "cleanup_last_alert", "0") or 0) > 86400:
        total = sum(cat["total"] for cat in rep["categories"])
        notify("Lupa · mało miejsca na dysku",
               f"Wolne {d['free'] / 1e9:.0f} GB ({d['free_pct']}%). Porządki znalazły {total / 1e9:.1f} GB do odzyskania.")
        meta_set(c, "cleanup_last_alert", time.time())
        c.commit()
