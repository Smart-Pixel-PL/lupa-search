"""Speech-to-text for audio and video with local Whisper (MLX, Apple Silicon).

Run:  uv run python -m lupa.transcribe   (the server starts it when the indexer is idle — both use the GPU)
Spoken text becomes searchable (FTS + text vectors, parts 20+) and is shown with timestamps in the preview.
Music, silence and Whisper's typical hallucinations on silence are filtered out.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from .common import CFG, DATA, V_TEXT, connect

PID = DATA / "transcribe.pid"
MODEL = CFG.get("whisper_model", "mlx-community/whisper-large-v3-turbo")
MAX_SECONDS = int(CFG.get("transcribe_max_minutes", 20)) * 60
ASR_PART = 20
MARK = "\n[TRANSKRYPCJA]\n"
HALLUCINATIONS = re.compile(r"(napisy (stworzone|wykonane)|amara\.org|dziękuję za (obejrzenie|uwagę)|subskrybuj|"
                            r"thanks for watching|subtitles by|www\.|zapraszam do subskrypcji)", re.I)


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS transcripts(file_id INTEGER PRIMARY KEY, lang TEXT, text TEXT,
                   segments TEXT, created REAL)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(files)")}
    if "asr" not in cols:  # 1 = speech found, -1 = no speech / music
        con.execute("ALTER TABLE files ADD COLUMN asr INTEGER")
    con.commit()


def extract_audio(path, dest):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-t", str(MAX_SECONDS), "-vn", "-ac", "1",
                        "-ar", "16000", dest], capture_output=True, timeout=900)
    return r.returncode == 0 and os.path.getsize(dest) > 32000


def clean_segments(segs):
    out = []
    for s in segs:
        t = s["text"].strip()
        if (not t or s.get("no_speech_prob", 0) > 0.6 or s.get("avg_logprob", 0) < -1.0
                or s.get("compression_ratio", 0) > 2.4 or HALLUCINATIONS.search(t)):
            continue
        out.append({"start": round(s["start"], 1), "end": round(s["end"], 1), "text": t})
    return out


def run():
    import mlx_whisper
    from .embedder import Embedder
    from .extract import chunks

    PID.write_text(str(os.getpid()))
    con = connect()
    ensure_schema(con)
    emb = Embedder(audio=False, vision=False, device="cpu")
    rows = con.execute("""SELECT id, path, name, kind FROM files WHERE kind IN ('audio','video') AND asr IS NULL
                          AND size > 50000 ORDER BY (root LIKE '/Volumes/%'), mtime DESC""").fetchall()
    print(f"transkrypcja: {len(rows)} nagrań w kolejce", flush=True)
    done = speech = 0
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for r in rows:
                if (DATA / "indexer.pid").exists():
                    print("transkrypcja: indeksator znów pracuje — przerywam, dokończę później", flush=True)
                    break
                if not os.path.exists(r["path"]):
                    continue
                wav = os.path.join(tmp, "a.wav")
                try:
                    if not extract_audio(r["path"], wav):
                        raise ValueError("brak ścieżki dźwiękowej")
                    res = mlx_whisper.transcribe(wav, path_or_hf_repo=MODEL, condition_on_previous_text=False)
                    segs = clean_segments(res.get("segments", []))
                except Exception as e:  # noqa: BLE001
                    segs, res = [], {}
                    if "brak ścieżki" not in str(e):
                        print("transkrypcja:", r["path"], e, flush=True)
                text = " ".join(s["text"] for s in segs)
                if len(text) < 40:  # music, noise, a few words — not worth indexing
                    con.execute("UPDATE files SET asr=-1 WHERE id=?", (r["id"],))
                else:
                    con.execute("INSERT OR REPLACE INTO transcripts(file_id,lang,text,segments,created) VALUES(?,?,?,?,?)",
                                (r["id"], res.get("language"), text, json.dumps(segs, ensure_ascii=False), time.time()))
                    ch = chunks(text)
                    vecs = emb.documents([os.path.splitext(r["name"])[0]] * len(ch), ch)
                    con.execute(f"DELETE FROM vectors WHERE file_id=? AND vtype={V_TEXT} AND part>={ASR_PART}", (r["id"],))
                    con.executemany("INSERT INTO vectors(file_id,vtype,part,vec) VALUES(?,?,?,?)",
                                    [(r["id"], V_TEXT, ASR_PART + k, v.tobytes()) for k, v in enumerate(vecs)])
                    body = (con.execute("SELECT body FROM fts WHERE rowid=?", (r["id"],)).fetchone() or [""])[0] or ""
                    con.execute("UPDATE fts SET body=? WHERE rowid=?", (body.split(MARK)[0] + MARK + text, r["id"]))
                    con.execute("UPDATE files SET asr=1, snippet=coalesce(nullif(snippet,''), ?) WHERE id=?",
                                (text[:320], r["id"]))
                    speech += 1
                con.commit()
                done += 1
                if done % 25 == 0:
                    print(f"transkrypcja: {done}/{len(rows)}, z mową {speech} ({done / max(time.time() - t0, 1):.2f}/s)",
                          flush=True)
    finally:
        PID.unlink(missing_ok=True)
    print(f"transkrypcja: gotowe — {done} nagrań, mowa w {speech}", flush=True)


if __name__ == "__main__":
    sys.exit(run())
