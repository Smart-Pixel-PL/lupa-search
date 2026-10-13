#!/bin/bash
# Prints one line ONLY when indexing needs attention (for Claude's Monitor): frozen, stopped with work left,
# server down, new errors in the log — and when everything is finished. Silent while all is well.
cd "$(dirname "$0")/.."
DB=data/lupa.db
log_pos=$(wc -c < data/indexer.log)
stale=0; dead=0; down=0; done_sent=0
while true; do
  st=$(curl -s -m 10 -H "x-lupa: 1" http://127.0.0.1:7766/api/status || true)
  if [ -z "$st" ]; then
    down=$((down+1)); [ $down -eq 3 ] && echo "ALARM: serwer Lupy nie odpowiada od ~1,5 min"
  else
    [ $down -ge 3 ] && echo "OK: serwer znów odpowiada"; down=0
    read run age left asr <<<"$(python3 -c '
import json,sys,time; d=json.loads(sys.argv[1]); i=d.get("indexer") or {}
print(int(bool(i.get("running"))), int(time.time()-(i.get("updated") or time.time())), d.get("stages",{}).get("1",0), 0)' "$st" 2>/dev/null || echo "0 0 0 0")"
    if [ "$run" = 1 ] && [ "$age" -gt 1200 ]; then
      [ $stale -eq 0 ] && echo "ALARM: indeksator działa, ale bez postępu od $((age/60)) min (zostało $left)"; stale=1
    else stale=0; fi
    if [ "$run" = 0 ] && [ "$left" -gt 0 ] && [ ! -e data/pause ]; then
      dead=$((dead+1)); [ $dead -eq 26 ] && echo "ALARM: indeksator nie pracuje od ~13 min, a zostało $left plików"
    else dead=0; fi
    if [ "$left" = 0 ] && [ $done_sent -eq 0 ]; then
      n=$(sqlite3 -readonly $DB "SELECT count(*) FROM files WHERE kind IN ('audio','video') AND asr IS NULL AND size>50000" 2>/dev/null)
      echo "KONIEC: analiza treści zakończona; transkrypcja — w kolejce $n nagrań"; done_sent=1
    fi
  fi
  # new error lines since last check (MPS out-of-memory retries are handled automatically — skipped)
  size=$(wc -c < data/indexer.log)
  if [ "$size" -lt "$log_pos" ]; then log_pos=0; fi
  if [ "$size" -gt "$log_pos" ]; then
    tail -c +$((log_pos+1)) data/indexer.log | grep -E "Traceback|Error|error|Fatal|fatal|Killed|abort|Segmentation|no progress" \
      | grep -v -E "batch encode failed|MPS backend out of memory|DeprecationWarning|fitz" | cut -c1-220 | sed 's/^/BŁĄD w logu: /' | tail -3
    log_pos=$size
  fi
  sleep 30
done
