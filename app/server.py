"""Mari kiosk English TTS - an OpenAI/Kokoro-compatible speech server.

Drop-in for the kiosk's remote-Kokoro path. The kiosk (test/Mari-Interactive-Kiosk/server/providers.py,
_tts_kokoro) sends:

    POST {APP_REMOTE_TTS_API_BASE}{APP_REMOTE_TTS_API_PATH or /v1/audio/speech}
    Authorization: Bearer <APP_REMOTE_TTS_TOKEN>        (only if the token is set)
    {"model": "kokoro", "input": "<text>", "voice": "<name>", "response_format": "mp3"}

and expects raw audio bytes back with a Content-Type it can read. That is exactly what this serves, so
pointing the kiosk here needs no kiosk code change - only environment variables.

Endpoints
    POST /v1/audio/speech     the kiosk's call. Returns audio bytes.
    GET  /v1/models           OpenAI-style model list
    GET  /v1/audio/voices     what voices exist, and which aliases map to them
    GET  /health              readiness and the loaded state
    POST /speak               same as /v1/audio/speech, with a JSON body + metadata headers
"""
import logging
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from settings import load_settings

# Reads settings.env (next to this file, or MARI_SETTINGS_FILE) into os.environ for any key not
# already set by the real environment - so a docker-compose `environment:` entry, or a real
# `export`, still wins, and editing the file is enough for everything else. Must run before
# `import tts`: its module-level constants (MODEL_DIR etc.) read os.environ at import time.
load_settings()

import tts  # noqa: E402

logging.basicConfig(level=os.environ.get("MARI_LOG", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mari-tts")

TOKEN = os.environ.get("MARI_TTS_TOKEN", "").strip()
DEFAULT_FORMAT = os.environ.get("MARI_DEFAULT_FORMAT", "mp3")
USE_LEXICON = os.environ.get("MARI_USE_LEXICON", "1") not in ("0", "false", "False", "")
NUM_STEP = int(os.environ.get("MARI_NUM_STEP", "32"))
# Speed defaults are per-voice (MARI_SPEED_MALE / MARI_SPEED_FEMALE / MARI_SPEED_<VOICE_ID>,
# falling back to MARI_DEFAULT_SPEED) - resolved in tts.Engine.default_speed() once the voice
# is known, not here, since a male and a female voice often want different rates.

app = FastAPI(title="Mari kiosk English TTS", version="1.0",
              description="OmniVoice behind an OpenAI/Kokoro-compatible API. "
                          "WEIGHTS ARE CC-BY-NC (non-commercial).")
ENGINE = None


class SpeechRequest(BaseModel):
    input: str = Field("", description="the text to speak")
    model: str = "kokoro"
    voice: str | None = None
    response_format: str | None = None
    speed: float | None = None
    # extensions, both optional and safely ignored by the kiosk
    lexicon: bool | None = Field(None, description="write Pakistani names in Urdu script")
    num_step: int | None = None
    guidance_scale: float | None = None
    text: str | None = Field(None, description="accepted as an alias for `input`")


@app.on_event("startup")
def _startup():
    global ENGINE
    warm = os.environ.get("MARI_WARMUP", "1") not in ("0", "false", "")
    ENGINE = tts.Engine(warmup=warm)


def _check_token(authorization):
    """Only enforced when MARI_TTS_TOKEN is set, matching the kiosk's optional-token behaviour."""
    if not TOKEN:
        return
    want = f"Bearer {TOKEN}"
    if (authorization or "").strip() != want:
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


def _speak(req: SpeechRequest, authorization):
    _check_token(authorization)
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="engine still loading")
    text = (req.input or req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="no input text")
    fmt = (req.response_format or DEFAULT_FORMAT).lower()
    use_lex = USE_LEXICON if req.lexicon is None else bool(req.lexicon)
    t0 = time.time()
    try:
        wav, meta = ENGINE.speak(text, voice=req.voice, speed=req.speed,
                                 num_step=req.num_step or NUM_STEP, use_lexicon=use_lex,
                                 guidance_scale=req.guidance_scale)
    except Exception as e:
        # A CUDA OOM is this card's shared-tenancy with a2f, not a bug in the request - say so
        # plainly rather than handing the kiosk a raw PyTorch stack trace as a 500.
        if "out of memory" in str(e).lower():
            log.error("CUDA OOM on %d chars (%s): %s", len(text), req.voice, e)
            # A deep PyTorch OOM's traceback holds a reference to every intermediate CUDA
            # tensor in every stack frame it passed through (attention projections, MLP
            # activations, ...) - and Python keeps a traceback, and everything it references,
            # alive for as long as anything holds the exception. `raise ... from e` would
            # chain it onto the HTTPException that escapes this function and that FastAPI's
            # own error handling then logs, keeping those tensors live well past this request.
            # Measured: each failed request was leaking VRAM into the next one as a result.
            # Clearing the traceback and re-emptying the cache here, then raising a clean
            # exception with no chain, is what actually frees it.
            e.__traceback__ = None
            if ENGINE is not None and ENGINE.device.startswith("cuda"):
                import torch
                torch.cuda.empty_cache()
            raise HTTPException(status_code=503,
                                detail="GPU memory is exhausted right now (this card also runs "
                                       "Audio2Face) - retry shortly") from None
        raise
    if not len(wav):
        raise HTTPException(status_code=500, detail="no audio produced")
    data, mime = tts.encode(wav, fmt)
    total = (time.time() - t0) * 1000
    log.info("spoke %d chars as %s in %.0f ms (%.2fs audio, rtf %.2f) swapped=%s",
             len(text), meta["voice"], total, meta["seconds"], meta["rtf"],
             ",".join(meta["urdu_swapped"]) or "-")
    headers = {
        "X-Voice": meta["voice"],
        "X-Speed": str(meta["speed"]),
        "X-Audio-Seconds": str(meta["seconds"]),
        "X-Generate-Ms": str(meta["ms_gen"]),
        "X-Total-Ms": f"{total:.0f}",
        "X-RTF": str(meta["rtf"]),
        "X-Sentences": str(meta["sentences"]),
        "X-Urdu-Swapped": ",".join(meta["urdu_swapped"]),
        "Cache-Control": "no-store",
    }
    return Response(content=data, media_type=mime, headers=headers)


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest, authorization: str | None = Header(default=None)):
    return _speak(req, authorization)


@app.post("/speak")
def speak(req: SpeechRequest, authorization: str | None = Header(default=None)):
    return _speak(req, authorization)


@app.get("/v1/models")
def models():
    return {"object": "list",
            "data": [{"id": "kokoro", "object": "model", "owned_by": "mari-tts-server"},
                     {"id": "omnivoice", "object": "model", "owned_by": "mari-tts-server"}]}


@app.get("/v1/audio/voices")
def voices():
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="engine still loading")
    return {"voices": [{"id": v, "gender": ENGINE.registry[v].get("gender"),
                        "label": ENGINE.registry[v].get("label"),
                        "reference_seconds": ENGINE.registry[v].get("duration")}
                       for v in sorted(ENGINE.prompts)],
            "default_male": ENGINE.default_male,
            "default_female": ENGINE.default_female,
            "aliases": tts.VOICE_ALIASES,
            "note": "Kokoro-style names also work: af_*/bf_* pick the female voice, am_*/bm_* the male."}


@app.get("/health")
def health():
    ready = ENGINE is not None
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready,
                 "device": getattr(ENGINE, "device", None),
                 "load_seconds": round(getattr(ENGINE, "load_s", 0), 1),
                 "voices": sorted(getattr(ENGINE, "prompts", {})),
                 "lexicon_entries": len(getattr(getattr(ENGINE, "lexicon", None), "map", {})),
                 "default_format": DEFAULT_FORMAT,
                 "lexicon_on": USE_LEXICON,
                 "auth_required": bool(TOKEN),
                 "licence": "OmniVoice weights are CC-BY-NC (non-commercial)"})


@app.get("/")
def root():
    return {"service": "Mari kiosk English TTS",
            "post": "/v1/audio/speech",
            "voices": "/v1/audio/voices",
            "health": "/health"}
