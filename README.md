# mari-tts-server — English TTS for the Mari kiosk, Pakistani accent, male and female

A deployable server that speaks **English in a Pakistani accent**, in a male and a female voice, and is a
**drop-in replacement for the kiosk's remote-Kokoro endpoint**. Pointing the kiosk here needs **no kiosk
code change** — only environment variables.

Pakistani names are spoken correctly because they are rewritten into **Urdu script** before synthesis:
gas fields, people, and the words *Pakistan* / *Pakistani*. 116 entries, built from
`mari_energies_knowledge_base.md`.

> **Licence — read this before deploying.** The model is `k2-fsa/OmniVoice`: its code is Apache 2.0 but
> its **weights are CC-BY-NC (non-commercial)**. That is a real blocker for a commercial kiosk. If this
> passes the listening test, the clean swap is `ResembleAI/chatterbox` (MIT) behind the same API — the
> server, lexicon and kiosk wiring would all stay as they are.

## First-time setup on a new machine (after `git clone`)

The model weights (3.1 GB) are **not in git** (see `.gitignore`) — download them from Hugging Face
into `models/omnivoice/` before the first run:

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download k2-fsa/OmniVoice --local-dir models/omnivoice
```

That's the only extra step. Everything else needed to run the server (voice prompts, lexicon,
code, Docker files) is already in the repo.

## Run it

```bash
cp .env.example .env            # optional; set MARI_TTS_TOKEN if you want auth
docker compose up -d --build
curl -s localhost:8080/health | python3 -m json.tool
bash selftest.sh                # speaks the same sentence in all four voice spellings
```

First start takes ~30 s: the model loads (~2 s) and both voices are warmed so the first real request is
not slow. The weights are **bind-mounted, not baked into the image**, so the image stays small and you can
replace them without a rebuild.

Without Docker (GPU on the host, torch 2.8 required):

```bash
PY=~/Desktop/kaani/envs/venv-omnivoice/bin/python
$PY -m uvicorn server:app --app-dir app --host 0.0.0.0 --port 8080
```

## Wire the kiosk to it

The kiosk already has a remote-Kokoro path (`test/Mari-Interactive-Kiosk/server/providers.py`,
`_tts_kokoro`). Set these in the kiosk's environment — full list in `kiosk.env.example`:

```bash
APP_EN_TTS=remote
APP_REMOTE_TTS_API_BASE=http://127.0.0.1:8080
APP_REMOTE_TTS_API_PATH=/v1/audio/speech
APP_REMOTE_TTS_MODEL=kokoro
APP_REMOTE_TTS_RESPONSE_FORMAT=mp3
APP_TTS_VOICE_ENGLISH=mari_female      # or mari_male
```

Urdu is untouched — it stays on Uplift (`APP_UPLIFT_*`). This server is English only.

## The API

`POST /v1/audio/speech` — exactly the request the kiosk already sends:

```json
{"model": "kokoro", "input": "...", "voice": "af_heart", "response_format": "mp3"}
```

Returns raw audio bytes with the right `Content-Type`, plus diagnostics in headers:

| Header | Meaning |
|---|---|
| `X-Voice` | which registered voice actually spoke |
| `X-Urdu-Swapped` | the names rewritten into Urdu script for this request |
| `X-Audio-Seconds`, `X-Generate-Ms`, `X-RTF`, `X-Sentences`, `X-Total-Ms` | timings |

Formats: `mp3` (default), `wav`, `opus`, `flac`, `aac`, `pcm`. Everything except `wav`/`pcm` goes through
ffmpeg; if ffmpeg is missing the server falls back to `wav` rather than failing.

Other endpoints: `GET /health`, `GET /v1/models`, `GET /v1/audio/voices`, and `POST /speak` (an alias).

Optional extras in the body, which the kiosk never sends and does not need to:
`{"lexicon": false}` to disable the Urdu rewriting for one request, `{"num_step": 16}` to trade quality
for speed, `{"speed": 1.1}`.

## Voices

| Ask for | You get |
|---|---|
| `mari_female`, `female`, `en_female` | **Lullaby** — the voice the kiosk already ships |
| `mari_male`, `male`, `en_male` | **UpliftAI prime-time anchor** — the documented kiosk male target |
| `af_heart`, `bf_*` (any Kokoro female name) | the female voice |
| `am_adam`, `bm_*` (any Kokoro male name) | the male voice |
| anything unrecognised | the female voice |

Kokoro's naming is `<accent><gender>_<name>`, so the `f`/`m` in the second position is what gets read.
That means the kiosk's stock default (`af_heart`) works untouched.

Both voices are **cloned from Urdu reference clips on purpose.** OmniVoice's documentation states that in
cross-lingual cloning the output carries the accent of the reference audio's language — that is the whole
mechanism by which English comes out Pakistani-accented. The clips and their pre-encoded prompts are in
`voices/`.

## How a request is handled

```
 text from the kiosk
   -> lexicon       Pakistani names rewritten in Urdu script (Fauji -> فوجی, Pakistan -> پاکستان)
   -> normalise     acronyms spaced (OGDCL -> O G D C L), numbers spelled out, markdown stripped
   -> split         into sentences, so no single generation runs long
   -> generate      one sentence at a time, cached voice prompt, GPU lock
   -> join          with a 220 ms gap
   -> encode        mp3 (or whatever response_format asked for)
```

Measured on a 6 GB RTX 4050: **1.9 GiB VRAM**, RTF **0.3–0.5**, so a 10 s answer takes ~4.7 s end to end
including mp3 encoding. The 18–26 s voice-prompt encode is **not** paid per request — the prompts are
pre-encoded in `voices/<id>/prompt.pt` and loaded at startup.

## Settings

Everything day-to-day-tunable lives in one file: **`app/settings.env`**. Edit it, then restart to apply:

```bash
docker compose restart mari-tts          # Docker
pkill -f "uvicorn server:app"; <rerun the same uvicorn command>   # running directly on the host
```

No rebuild needed — it's bind-mounted into the container (`docker-compose.yml`), and the server reads it
fresh on every startup (`app/settings.py`, loaded before anything else). A real exported env var or a
docker-compose `environment:` entry still overrides the file, same convention the kiosk's own `.env`
loader uses.

Covers: `MARI_NUM_STEP` (quality vs. speed), `MARI_DEFAULT_SPEED`, `MARI_GAP_MS` (pause between
sentences), `MARI_MAX_CHARS` (chunk size), `MARI_USE_LEXICON` (on/off), `MARI_DEVICE` (`cuda:0`/`cpu`),
`MARI_VRAM_FRACTION` (the GPU-sharing ceiling — see "Things to know"), `MARI_DEFAULT_FORMAT`,
`MARI_WARMUP`, `MARI_TTS_TOKEN`, `MARI_LOG`. Each one is commented in the file with its measured-safe
default and what changing it trades off.

## The lexicon

`app/lexicon.json`, 116 entries in three groups, all checked against the knowledge base:

| Group | Count | Examples |
|---|---|---|
| people | 56 | `Anwar Ali Hyder → انور علی حیدر`, `Faheem Haider → فہیم حیدر`, `Fauji → فوجی` |
| places / gas fields | 58 | `Mari → ماڑی`, `Sujawal → سجاول`, `Daharki → ڈہرکی`, `Sachal → سچل`, `Waziristan → وزیرستان` |
| country | 2 | `Pakistan → پاکستان`, `Pakistani → پاکستانی` |

Matching is longest-first on word boundaries, so `Anwar Ali Hyder` wins over `Hyder`. Rebuild it with:

```bash
python app/build_lexicon.py          # reports KB names that still have no Urdu spelling
```

The builder prints any knowledge-base name it does not cover, so the lexicon cannot silently fall behind
the KB. The 38 it currently reports are ordinary English words or foreign brands (`Kubernetes`, `Esso`,
`Dundee`) and are deliberately left alone — see `SKIP` in the builder.

Edit `app/lexicon.json` and restart the container to pick it up; it is mounted, so no rebuild.

## Folder map

| Path | What it is |
|---|---|
| `app/server.py` | the FastAPI app: the OpenAI/Kokoro-compatible endpoints |
| `app/tts.py` | the engine: model load, prompt cache, lexicon, normalisation, sentence split, encoding |
| `app/build_lexicon.py` | builds `lexicon.json` from the knowledge base and reports gaps |
| `app/lexicon.json` | the 116 Roman → Urdu entries (mounted into the container) |
| `models/omnivoice/` | 3.1 GB of weights, bind-mounted read-only |
| `voices/` | `registry.json` + per-voice clip, transcript and pre-encoded `prompt.pt` |
| `Dockerfile`, `docker-compose.yml` | the deployment |
| `kiosk.env.example` | the variables to set on the kiosk side |
| `selftest.sh` | proves the kiosk's exact request works in all four voice spellings |

## Things to know

- **The weights are hardlinked** from `~/Desktop/omnivoice-tts/models/omnivoice`, so they cost no extra
  disk. Hardlinks keep the data alive even if that folder is deleted. To get an independent copy:
  `cp -r --remove-destination`.
- **GPU is expected but not required.** Set `MARI_DEVICE=cpu` to run without one; it will be far slower.
- **One generation at a time.** A 6 GB card cannot take two, so the engine serialises on a lock —
  concurrent kiosk requests queue rather than OOM.
- **Auth is off unless you set `MARI_TTS_TOKEN`**, which matches the kiosk's own behaviour of sending the
  bearer header only when its token is configured.
- **The accent is still unverified by ear.** The server is correct and fast; whether it *sounds*
  Pakistani is the open question. Listening pages for exactly that are in `~/Desktop/omnivoice-tts`, and
  `~/Desktop/omnivoice-tts/FINDINGS.md` carries the full background (including why Veena was rejected
  and the reference-clip rule that must not be broken).
