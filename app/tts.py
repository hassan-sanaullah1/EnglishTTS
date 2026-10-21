"""The TTS engine: OmniVoice loaded once, voice prompts cached, text normalised, Urdu names swapped in.

Pipeline for every request:
    text -> lexicon (Urdu script for Pakistani names) -> normalise (acronyms, numbers)
         -> split into sentences -> generate each -> join -> encode to the asked-for format

Voice prompts are pre-encoded (voices/<id>/prompt.pt), so the 18-26 s encode never happens at request
time; per-request cost is generation only (RTF 0.15-0.70 on a 6 GB RTX 4050).
"""
import json
import logging
import os
import re
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("mari-tts")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODEL_DIR = os.environ.get("MARI_MODEL_DIR", os.path.join(ROOT, "models", "omnivoice"))
VOICES_DIR = os.environ.get("MARI_VOICES_DIR", os.path.join(ROOT, "voices"))
LEXICON_PATH = os.environ.get("MARI_LEXICON", os.path.join(HERE, "lexicon.json"))
SR = 24000

# What the kiosk may send as "voice" -> our registered voice id.
# The kiosk's default config value is a Kokoro name (af_heart), so Kokoro names must map too:
# Kokoro's convention is <accent><gender>_<name>, so af_/bf_ = female and am_/bm_ = male.
VOICE_ALIASES = {
    "male": "uplift_male", "female": "lullaby_female",
    "en_male": "uplift_male", "en_female": "lullaby_female",
    "mari_male": "uplift_male", "mari_female": "lullaby_female",
}

ACRONYMS = ["OGDCL", "SNGPL", "PSO", "PPL", "KPMG", "ACCA", "MPCL", "PSX", "GEM", "GHG", "LNG",
            "REE", "REEs", "ESG", "MD", "CEO", "CFO", "COO", "AI", "UET", "LUMS", "IBA", "SOAS",
            "UCLA", "SDG", "SDGs", "HSE", "ERP", "EPS", "EBITDA", "API", "ISO", "CSR", "AGM"]
ABBREV = ["mr", "mrs", "ms", "dr", "prof", "lt", "gen", "col", "brig", "maj", "capt", "rev", "hon",
          "st", "jr", "sr", "no", "vs", "etc", "inc", "ltd", "pvt", "co", "corp", "approx", "dept",
          "est", "fig", "govt", "e.g", "i.e"]
NUM_SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
             "eighteen", "nineteen"]
NUM_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _int_words(n):
    if n < 0:
        return "minus " + _int_words(-n)
    if n < 20:
        return NUM_SMALL[n]
    if n < 100:
        return NUM_TENS[n // 10] + ("" if n % 10 == 0 else " " + NUM_SMALL[n % 10])
    if n < 1000:
        return NUM_SMALL[n // 100] + " hundred" + ("" if n % 100 == 0
                                                   else " and " + _int_words(n % 100))
    for div, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if n >= div:
            return (_int_words(n // div) + " " + name
                    + ("" if n % div == 0 else " " + _int_words(n % div)))
    return str(n)


def _num_sub(m):
    whole = m.group(0).replace(",", "")
    if re.fullmatch(r"(19|20)\d{2}", whole):
        n = int(whole)
        if n % 100 == 0:
            return _int_words(n // 100) + " hundred"
        return _int_words(n // 100) + " " + (_int_words(n % 100) if n % 100 >= 10
                                             else "oh " + _int_words(n % 100))
    if "." in whole:
        a, b = whole.split(".", 1)
        return _int_words(int(a)) + " point " + " ".join(_int_words(int(d)) for d in b)
    return _int_words(int(whole))


class Lexicon:
    """Roman -> Urdu script, longest match first, on word boundaries."""

    def __init__(self, path=LEXICON_PATH):
        self.path = path
        self.map = {}
        self.pattern = None
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.map = json.load(f)
        else:
            log.warning("no lexicon at %s; names will stay in Roman letters", self.path)
            self.map = {}
        if self.map:
            keys = sorted(self.map, key=len, reverse=True)
            self.pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b")
        return len(self.map)

    def apply(self, text):
        if not self.pattern:
            return text, []
        hits = []

        def sub(m):
            hits.append(m.group(1))
            return self.map[m.group(1)]
        return self.pattern.sub(sub, text), hits


def normalise(text):
    t = text.strip()
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_#`>|]+", " ", t)
    t = t.replace("&", " and ").replace("%", " percent")
    t = re.sub(r"\bPKR\b", "rupees", t)
    t = re.sub(r"[‘’]", "'", t)
    t = re.sub(r"[“”]", '"', t)
    t = re.sub(r"[–—]", ", ", t)
    for a in sorted(ACRONYMS, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(a)}\b", " ".join(a), t)
    t = re.sub(r"\b\d[\d,]*(\.\d+)?\b", _num_sub, t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ([,.;:?!])", r"\1", t)
    return t.strip()


def split_sentences(text, max_chars=240):
    """Sentences, then commas if one is still too long. Recognises Urdu punctuation too
    (۔ full stop, ؟ question mark, ، comma) for code-switched Urdu+English text - without
    this, a plain Urdu sentence ending in "۔" was never split, so the whole reply went to the
    model as one generation instead of per-sentence chunks."""
    t = re.sub(r"\s+", " ", text.strip())
    if not t:
        return []
    for a in ABBREV:
        t = re.sub(rf"(?i)\b({re.escape(a)})\.", r"\1<DOT>", t)
    out = []
    for p in [x.strip() for x in re.split(r"(?<=[.!?۔؟])\s+", t) if x.strip()]:
        p = p.replace("<DOT>", ".")
        if len(p) <= max_chars:
            out.append(p)
            continue
        buf = ""
        for piece in re.split(r"(?<=[,،])\s+", p):
            if buf and len(buf) + len(piece) + 1 > max_chars:
                out.append(buf.strip())
                buf = piece
            else:
                buf = f"{buf} {piece}".strip()
        if buf:
            out.append(buf.strip())
    return out or [t.replace("<DOT>", ".")]


def encode(wav, fmt):
    """float32 @24 kHz -> bytes in the asked-for container, plus its MIME type."""
    import io
    import wave
    pcm = (np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes()
    if fmt == "pcm":
        return pcm, "audio/pcm"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    wav_bytes = buf.getvalue()
    if fmt == "wav":
        return wav_bytes, "audio/wav"
    codec = {"mp3": ("mp3", "audio/mpeg", ["-b:a", "64k"]),
             "opus": ("opus", "audio/ogg", ["-b:a", "48k"]),
             "flac": ("flac", "audio/flac", []),
             "aac": ("adts", "audio/aac", ["-b:a", "64k"])}.get(fmt)
    if not codec:
        return wav_bytes, "audio/wav"
    f_fmt, mime, extra = codec
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                        "-f", f_fmt, *extra, "pipe:1"],
                       input=wav_bytes, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        log.warning("ffmpeg failed for %s (%s); returning wav", fmt, p.stderr[:200])
        return wav_bytes, "audio/wav"
    return p.stdout, mime


class Engine:
    def __init__(self, model_dir=MODEL_DIR, voices_dir=VOICES_DIR, device=None, warmup=True):
        import torch
        from omnivoice import OmniVoice, VoiceClonePrompt
        self.device = device or os.environ.get("MARI_DEVICE",
                                               "cuda:0" if torch.cuda.is_available() else "cpu")
        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        missing = [f for f in ("config.json", "model.safetensors")
                   if not os.path.exists(os.path.join(model_dir, f))]
        if missing:
            raise RuntimeError(f"model weights missing from {model_dir}: {missing}")

        # This GPU also runs Audio2Face (audio-driven lip-sync avatar), which has already OOM'd
        # once in production (CUDA error: out of memory, during a track reset) on a 6 GB laptop
        # card shared with us. We measured our own peak at only +30-40 MiB above idle during
        # generation - we are not the one ballooning - but a hard ceiling means we can never be
        # the one that starves a2f, converting "whoever grows first wins" into a predictable
        # budget. Set MARI_VRAM_FRACTION=0 to disable (e.g. if this is ever the only GPU tenant).
        if self.device.startswith("cuda"):
            frac = float(os.environ.get("MARI_VRAM_FRACTION", "0.42"))
            if frac > 0:
                torch.cuda.set_per_process_memory_fraction(frac, device=self.device)
                log.info("VRAM ceiling: %.0f%% of device (%.1f GiB) - leaves the rest for "
                         "other GPU tenants (a2f, etc.)", frac * 100,
                         torch.cuda.get_device_properties(self.device).total_memory
                         * frac / 2**30)

        t0 = time.time()
        self.model = OmniVoice.from_pretrained(model_dir, device_map=self.device, dtype=dtype,
                                               asr_device="cpu")
        self.load_s = time.time() - t0
        self.lexicon = Lexicon()
        self._gpu = threading.Lock()        # a 6 GB card will not take two generations at once

        reg_path = os.path.join(voices_dir, "registry.json")
        rows = json.load(open(reg_path, encoding="utf-8")) if os.path.exists(reg_path) else []
        self.registry = {r["id"]: r for r in rows}
        self.prompts = {}
        for vid in list(self.registry):
            pt = os.path.join(voices_dir, vid, "prompt.pt")
            if os.path.exists(pt):
                self.prompts[vid] = VoiceClonePrompt.load(pt)
            else:
                log.error("voice %s has no cached prompt at %s - dropping it", vid, pt)
                self.registry.pop(vid)
        if not self.prompts:
            raise RuntimeError(f"no usable voices in {voices_dir}")
        self.default_male = next((v for v, r in self.registry.items()
                                  if r.get("gender") == "male"), None)
        self.default_female = next((v for v, r in self.registry.items()
                                    if r.get("gender") == "female"), None)
        log.info("loaded in %.1fs on %s | voices %s | lexicon %d entries",
                 self.load_s, self.device, sorted(self.prompts), len(self.lexicon.map))
        if warmup:
            for vid in sorted(self.prompts):
                t = time.time()
                self._generate("Ready.", vid, 16, 1.0)
                log.info("warmed %s in %.1fs", vid, time.time() - t)

    def resolve_voice(self, name):
        """Map whatever the kiosk sent to a registered voice id."""
        if not name:
            return self.default_female or next(iter(self.prompts))
        n = str(name).strip()
        if n in self.prompts:
            return n
        low = n.lower()
        if low in VOICE_ALIASES and VOICE_ALIASES[low] in self.prompts:
            return VOICE_ALIASES[low]
        # Kokoro names: <accent><gender>_<name>, e.g. af_heart (female), am_adam (male)
        m = re.match(r"^[a-z]([fm])_", low)
        if m:
            want = "female" if m.group(1) == "f" else "male"
            pick = self.default_female if want == "female" else self.default_male
            if pick:
                return pick
        if "male" in low and "female" not in low and self.default_male:
            return self.default_male
        if "female" in low and self.default_female:
            return self.default_female
        return self.default_female or next(iter(self.prompts))

    def _generate(self, sentence, voice_id, num_step, speed, guidance_scale=None):
        with self._gpu:
            try:
                kw = dict(text=sentence, voice_clone_prompt=self.prompts[voice_id],
                         num_step=num_step, speed=speed)
                if guidance_scale is not None:
                    from omnivoice import OmniVoiceGenerationConfig
                    kw["generation_config"] = OmniVoiceGenerationConfig(
                        num_step=num_step, guidance_scale=guidance_scale)
                    del kw["num_step"]
                audio = self.model.generate(**kw)
            finally:
                # PyTorch's caching allocator holds freed blocks for its own reuse rather than
                # handing them back to the driver. That's normally right, but a2f shares this
                # card and needs genuinely free VRAM for its own bursts, not memory we are
                # merely willing to recycle - and on a CUDA OOM the partially-built tensors
                # from the failed forward pass must be released too, or a retried request (or
                # any later one) starts from a worse baseline than this one did. Measured: three
                # failed generations in a row on this card, each left more fragmentation than
                # the last, because this cleanup used to run only on the success path.
                if self.device.startswith("cuda"):
                    import torch
                    torch.cuda.empty_cache()
        return np.asarray(audio[0], dtype="float32")

    def default_speed(self, voice_id):
        """Per-voice default: MARI_SPEED_<VOICE_ID> if set, else MARI_SPEED_<GENDER>, else
        MARI_DEFAULT_SPEED, else 1.0. A male and a female voice often want different rates -
        this is the knob for that, without every caller having to know which voice it got."""
        row = self.registry.get(voice_id, {})
        for key in (f"MARI_SPEED_{voice_id.upper()}",
                   f"MARI_SPEED_{row.get('gender', '').upper()}",
                   "MARI_DEFAULT_SPEED"):
            v = os.environ.get(key)
            if v:
                try:
                    return float(v)
                except ValueError:
                    log.warning("ignoring non-numeric %s=%r", key, v)
        return 1.0

    def speak(self, text, voice=None, speed=None, num_step=32, use_lexicon=True,
              gap_s=None, max_chars=None, guidance_scale=None):
        vid = self.resolve_voice(voice)
        speed = speed if speed is not None else self.default_speed(vid)
        said, hits = self.lexicon.apply(text) if use_lexicon else (text, [])
        norm = normalise(said)
        gap_s = gap_s if gap_s is not None else float(os.environ.get("MARI_GAP_MS", "220")) / 1000
        max_chars = max_chars or int(os.environ.get("MARI_MAX_CHARS", "240"))
        if guidance_scale is None:
            gs_env = os.environ.get("MARI_GUIDANCE_SCALE", "")
            guidance_scale = float(gs_env) if gs_env else None
        sentences = split_sentences(norm, max_chars=max_chars)
        t0 = time.time()
        pieces = [self._generate(s, vid, num_step, speed, guidance_scale) for s in sentences]
        if not pieces:
            return np.zeros(0, "float32"), {}
        gap = np.zeros(int(gap_s * SR), "float32")
        out = pieces[0]
        for p in pieces[1:]:
            out = np.concatenate([out, gap, p])
        ms = (time.time() - t0) * 1000
        return out, dict(voice=vid, speed=speed, sentences=len(sentences), urdu_swapped=hits,
                         seconds=round(len(out) / SR, 2), ms_gen=round(ms, 1),
                         rtf=round(ms / 1000 / max(len(out) / SR, 1e-6), 3), reads=norm)

    def stream(self, text, voice=None, speed=None, num_step=32, use_lexicon=True):
        """Yield one sentence of audio at a time, so playback can start on the first."""
        vid = self.resolve_voice(voice)
        speed = speed if speed is not None else self.default_speed(vid)
        said, _ = self.lexicon.apply(text) if use_lexicon else (text, [])
        for s in split_sentences(normalise(said)):
            yield self._generate(s, vid, num_step, speed)
