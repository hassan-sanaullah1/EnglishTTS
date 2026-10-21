#!/usr/bin/env bash
# Prove the server answers the kiosk's exact request, in both voices, and that the lexicon fired.
#
# Usage:  bash selftest.sh [base-url]        (default http://127.0.0.1:8080)
set -euo pipefail
BASE=${1:-http://127.0.0.1:8080}
AUTH=()
[ -n "${MARI_TTS_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $MARI_TTS_TOKEN")
OUT=$(mktemp -d)

echo "== health =="
curl -fsS "${AUTH[@]}" "$BASE/health" | python3 -m json.tool

TEXT='Mari Energies is a Pakistani company. Our chairman is Lieutenant General Anwar Ali Hyder, who also chairs the Fauji Group of Companies. The Sujawal gas field is near Daharki in upper Sindh.'

for V in mari_female mari_male af_heart am_adam; do
  echo
  echo "== voice: $V =="
  curl -fsS "${AUTH[@]}" -X POST "$BASE/v1/audio/speech" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"model":"kokoro","input":sys.argv[1],"voice":sys.argv[2],"response_format":"mp3"}))' "$TEXT" "$V")" \
    -D "$OUT/h_$V.txt" -o "$OUT/$V.mp3"
  grep -iE '^(content-type|x-voice|x-urdu-swapped|x-audio-seconds|x-rtf|x-total-ms)' "$OUT/h_$V.txt"
  echo "  bytes: $(stat -c%s "$OUT/$V.mp3")"
done

echo
echo "audio written to $OUT - play them to check the accent and the names:"
ls -1 "$OUT"/*.mp3
