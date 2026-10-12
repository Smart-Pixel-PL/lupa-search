"""OCR of scans and text-heavy images with Apple Vision (on-device, Neural Engine, Polish supported).

Run:  uv run python -m lupa.ocr          (the server starts it in the background when there is work)
Candidates: PDFs with (almost) no text layer — scans — and images that look like documents or
screenshots (picked with Gemma: visual vectors close to "a scanned document with text").
Recognised text is added to full-text search and embedded as extra text vectors (parts 10+).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

from .common import DATA, PROJECT, V_NAME, V_TEXT, V_VISUAL, connect, meta_set, nfc

PID = DATA / "ocr.pid"
BIN = DATA / "bin" / "lupa-ocr"
OCR_PART = 10        # vector parts >= 10 hold OCR text (parts 0-3 are the original content)
MARK = "\n[OCR]\n"
MAX_PAGES = 3
DOC_PROMPTS = ["zeskanowany dokument z tekstem: faktura, umowa, pismo, formularz, paragon",
               "zrzut ekranu z dużą ilością tekstu"]


def ensure_binary():
    if BIN.exists():
        return True
    BIN.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["swiftc", "-O", str(PROJECT / "tools" / "ocr.swift"), "-o", str(BIN),
                        "-framework", "Vision", "-framework", "AppKit"], capture_output=True, text=True)
    return r.returncode == 0


def pick_image_candidates(con, emb, limit_z=2.2):
    """Images whose picture is close to 'document with text' (z-score among all image vectors)."""
    q = emb._enc([nfc(t) for t in DOC_PROMPTS], prompt_name="SearchQuery").astype(np.float32)
    # big enough to hold readable text; one copy per (name, size) — website exports repeat images many times
    rows = con.execute(f"""SELECT v.file_id, v.vec FROM vectors v JOIN files f ON f.id = v.file_id
                           WHERE v.vtype = {V_VISUAL} AND v.part = 0 AND f.kind = 'image' AND f.ocr IS NULL
                           AND max(coalesce(f.width,0), coalesce(f.height,0)) >= 700 AND f.path NOT LIKE '%/rwdMode_%'
                           AND f.id IN (SELECT min(id) FROM files WHERE kind='image' GROUP BY name, size)""").fetchall()
    if not rows:
        return []
    ids = np.array([r[0] for r in rows])
    m = np.frombuffer(b"".join(r[1] for r in rows), np.float16).reshape(len(rows), -1).astype(np.float32)
    s = (m @ q.T).max(1)
    z = (s - s.mean()) / (s.std() or 1)
    return [int(i) for i in ids[np.argsort(-z)]][: int((z >= limit_z).sum())]


def pages_for(path: str, kind: str, tmp: str):
    if kind != "pdf":
        return [path]
    import fitz
    out = []
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc):
            if i >= MAX_PAGES:
                break
            zoom = 2000 / max(page.rect.width, page.rect.height, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            f = os.path.join(tmp, f"p{os.getpid()}_{i}.png")
            pix.save(f)
            out.append(f)
    finally:
        doc.close()
    return out


def ocr_images(paths):
    if not paths:
        return []
    r = subprocess.run([str(BIN), *paths], capture_output=True, text=True, timeout=600)
    res = []
    for line in r.stdout.splitlines():
        try:
            res.append(json.loads(line))
        except ValueError:
            pass
    return res


def run():
    from .embedder import Embedder
    from .extract import chunks, snippet

    if not ensure_binary():
        print("ocr: nie udało się zbudować narzędzia OCR", flush=True)
        return 1
    PID.write_text(str(os.getpid()))
    con = connect()
    emb = Embedder(audio=False, vision=False, device="cpu")  # text model only, CPU
    done = found = 0
    t0 = time.time()
    try:
        # 1) scanned PDFs: analysed, but (almost) no text layer
        pdfs = [r[0] for r in con.execute("""SELECT f.id FROM files f WHERE f.kind='pdf' AND f.stage=2 AND f.ocr IS NULL
                                             AND (f.snippet IS NULL OR length(f.snippet) < 100) ORDER BY f.mtime DESC""")]
        # 2) document-like images and screenshots
        imgs = pick_image_candidates(con, emb)
        queue = pdfs + imgs
        print(f"ocr: {len(pdfs)} skanów PDF + {len(imgs)} obrazów z tekstem do odczytania", flush=True)
        meta_set(con, "ocr_queue", len(queue))
        con.commit()
        with tempfile.TemporaryDirectory() as tmp:
            for fid in queue:
                r = con.execute("SELECT id, path, name, kind, root FROM files WHERE id=?", (fid,)).fetchone()
                if not r or not os.path.exists(r["path"]):
                    continue
                try:
                    pages = pages_for(r["path"], r["kind"], tmp)
                    res = ocr_images(pages)
                except Exception as e:  # noqa: BLE001
                    con.execute("UPDATE files SET ocr=-1 WHERE id=?", (fid,))
                    con.commit()
                    print("ocr:", r["path"], e, flush=True)
                    continue
                text = "\n".join(x["text"] for x in res if x.get("conf", 0) >= 0.4).strip()
                if len(text) < 30:
                    con.execute("UPDATE files SET ocr=-1 WHERE id=?", (fid,))
                else:
                    ch = chunks(text)
                    vecs = emb.documents([os.path.splitext(r["name"])[0]] * len(ch), ch)
                    con.execute(f"DELETE FROM vectors WHERE file_id=? AND vtype={V_TEXT} AND part>={OCR_PART}", (fid,))
                    con.executemany("INSERT INTO vectors(file_id,vtype,part,vec) VALUES(?,?,?,?)",
                                    [(fid, V_TEXT, OCR_PART + k, v.tobytes()) for k, v in enumerate(vecs)])
                    body = (con.execute("SELECT body FROM fts WHERE rowid=?", (fid,)).fetchone() or [""])[0] or ""
                    body = body.split(MARK)[0] + MARK + text
                    con.execute("UPDATE fts SET body=? WHERE rowid=?", (body, fid))
                    con.execute("UPDATE files SET ocr=1, snippet=coalesce(nullif(snippet,''), ?) WHERE id=?",
                                (snippet(text), fid))
                    found += 1
                con.commit()
                done += 1
                if done % 50 == 0:
                    meta_set(con, "ocr_done", done)
                    con.commit()
                    print(f"ocr: {done}/{len(queue)}, z tekstem {found} ({done / max(time.time() - t0, 1):.1f}/s)", flush=True)
    finally:
        PID.unlink(missing_ok=True)
    print(f"ocr: gotowe — {done} plików, tekst znaleziony w {found}", flush=True)


if __name__ == "__main__":
    sys.exit(run())
