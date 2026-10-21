"""Loads settings.env into os.environ, without overriding anything already set.

Precedence, highest first: a real `export`-ed env var > docker-compose's `environment:` block >
this file > the hardcoded fallback in each os.environ.get(KEY, fallback) call. That matches the
convention server/config.py already uses on the kiosk side (its own `.env` loader), so the same
mental model applies in both repos.

Called once, at the very top of server.py, before `import tts` - tts.py reads several of these
at IMPORT time (MODEL_DIR, VOICES_DIR, LEXICON_PATH), so the file must be loaded first.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "settings.env")


def load_settings(path=None):
    path = path or os.environ.get("MARI_SETTINGS_FILE", DEFAULT_PATH)
    if not os.path.exists(path):
        return 0
    n = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


if __name__ == "__main__":
    n = load_settings()
    print(f"loaded {n} settings from {os.environ.get('MARI_SETTINGS_FILE', DEFAULT_PATH)}")
    for k in sorted(os.environ):
        if k.startswith("MARI_"):
            print(f"  {k}={os.environ[k]}")
