# Diane — Setup (Ubuntu / X11, Tier A CPU)

## System packages

```bash
sudo apt install portaudio19-dev python3.12-venv
# Qt runtime for the avatar (M8):
sudo apt install libxcb-cursor0 libegl1 libxkbcommon-x11-0
```

## Ollama (local LLM server, the only permitted network endpoint — localhost:11434)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct
ollama pull moondream
```

## Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[avatar,dev]"
# openwakeword 0.6.0 declares tflite-runtime (no Python 3.12 wheels); we use its
# ONNX path instead, so install without deps:
.venv/bin/pip install --no-deps openwakeword==0.6.0
.venv/bin/pip install tqdm scipy requests   # the openwakeword deps we do need
```

Wake/VAD model artifacts: `python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"`
plus `models/silero_vad.onnx` (see models.md).

## Models directory

Piper voices and the custom wake-word model live under `models/` (gitignored);
see `models.md` for exact artifacts and download commands.

## Run

```bash
.venv/bin/python -m jarvis.main
```

## Background service (M7)

```bash
systemctl --user enable --now diane.service   # unit installed by scripts/install_service.sh
```
