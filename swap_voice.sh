#!/usr/bin/env bash
# Swap a voice's reference clip, re-encode its prompt (leak-checked), and reload the server.
#
# The voice you hear is cloned from one short reference clip. Change the clip -> change the
# voice's timbre AND its delivery style (pace, pauses, energy). This script does the whole
# swap end to end: re-encode, validate, copy into place, restart the container.
#
# USAGE
#   ./swap_voice.sh female <clip.wav> "<Urdu transcript of the clip>"
#   ./swap_voice.sh male   <clip.wav> "<transcript>"
#
#   # or pick one of the already-cut candidate clips (no transcript needed, it's known):
#   ./swap_voice.sh female candidate QtsdIKxsPd8__0006.wav
#   ./swap_voice.sh --list-candidates female
#
# NOTES
#   - The transcript matters: it must be what the clip actually says, in Urdu script. For your
#     own clip, 3-10 s, ONE complete sentence, clean tail silence, no cut-off words (a mid-word
#     cut makes the model speak the reference's leftover words before your text).
#   - After it runs, the new voice is live. Undo = run it again with the old clip, or restore
#     from voices/<id>.bak which this script leaves behind.
set -euo pipefail
cd "$(dirname "$0")"

OTTS="$HOME/Desktop/omnivoice-tts"
PY="$HOME/Desktop/kaani/envs/venv-omnivoice/bin/python"
CAND_DIR_female="$OTTS/data/1_references/lullaby_female"
CAND_DIR_male="$OTTS/data/1_references/uplift_male"
CONTAINER="mari-tts-v2"

id_for() { case "$1" in female) echo lullaby_female;; male) echo uplift_male;; *) echo "$1";; esac; }

if [[ "${1:-}" == "--list-candidates" ]]; then
  G="${2:-female}"; VAR="CAND_DIR_$G"; DIR="${!VAR:-}"
  [[ -d "$DIR" ]] || { echo "no candidate folder for '$G'"; exit 1; }
  echo "Pre-cut $G candidates (clip  duration  tail_silence  spk_sim  text):"
  column -t -s$'\t' "$DIR/candidates.tsv"
  echo; echo "Use one with:  ./swap_voice.sh $G candidate <clip.wav>"
  exit 0
fi

GENDER="${1:?usage: ./swap_voice.sh female|male <clip.wav|candidate NAME> [\"transcript\"]}"
ID="$(id_for "$GENDER")"

# Resolve the wav + transcript, two ways to call it.
if [[ "${2:-}" == "candidate" ]]; then
  NAME="${3:?give a candidate clip name - see ./swap_voice.sh --list-candidates $GENDER}"
  VAR="CAND_DIR_$GENDER"; DIR="${!VAR}"
  WAV="$DIR/$NAME"
  TEXT="$(awk -F'\t' -v n="$NAME" '$1==n{print $5}' "$DIR/candidates.tsv")"
  [[ -n "$TEXT" ]] || { echo "no transcript for $NAME in candidates.tsv"; exit 1; }
else
  WAV="${2:?give a path to a .wav clip}"
  TEXT="${3:?give the Urdu transcript of the clip in quotes}"
fi
[[ -f "$WAV" ]] || { echo "clip not found: $WAV"; exit 1; }

echo ">> re-encoding '$ID' from $(basename "$WAV") (leak-checked)..."
"$PY" "$OTTS/src/1_voices/register_voice.py" \
    --wav "$WAV" --id "$ID" --label "$ID (swapped $(date +%F))" \
    --gender "$GENDER" --text "$TEXT"

echo ">> installing into this server's voices/ ..."
[[ -d "voices/$ID" ]] && rm -rf "voices/$ID.bak" && cp -r "voices/$ID" "voices/$ID.bak"
cp -r "$OTTS/voices/$ID/." "voices/$ID/"
cp "$OTTS/voices/registry.json" "voices/registry.json"

echo ">> restarting $CONTAINER ..."
docker restart "$CONTAINER" >/dev/null
echo ">> done. New '$ID' voice is live (old one saved in voices/$ID.bak)."
