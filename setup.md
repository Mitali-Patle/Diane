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
```

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
