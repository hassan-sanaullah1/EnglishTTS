# Mari kiosk English TTS - OmniVoice behind an OpenAI/Kokoro-compatible API.
#
# The 3.1 GB of model weights are NOT baked into the image: they are mounted from ./models at run time
# (see docker-compose.yml). That keeps the image at a sane size and lets you swap weights without a
# rebuild.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

# ffmpeg is what encodes mp3/opus/aac for response_format; without it the server falls back to wav
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# requirements first, so a code change does not re-install torch
COPY requirements.txt .
# torch/torchaudio are already in the base image at the right version; everything else is small
RUN pip install --no-cache-dir \
      omnivoice numpy soundfile fastapi "uvicorn[standard]" "pydantic>=2"

COPY app/ /srv/app/

# Fixed, structural (tied to the bind mounts above) - NOT the day-to-day tuning knobs. Those
# live in app/settings.env, loaded at startup by app/settings.py; a value baked in here as a
# real env var would silently outrank the file, so keep this list to paths and container
# plumbing only.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    MARI_MODEL_DIR=/srv/models/omnivoice \
    MARI_VOICES_DIR=/srv/voices \
    MARI_LEXICON=/srv/app/lexicon.json

EXPOSE 8080

# the model takes a few seconds to load and both voices are warmed at startup, so allow for that
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["uvicorn", "server:app", "--app-dir", "/srv/app", "--host", "0.0.0.0", "--port", "8080"]
