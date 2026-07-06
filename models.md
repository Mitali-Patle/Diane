# Model Table — Tier A (CPU-only, D-1 resolved 2026-07-06)

Hardware: i7-1360P, Iris Xe (no CUDA), 16 GB RAM. Tier selects models only —
never feature behavior (§8).

| Stage | Model | Artifact / source | Notes |
|---|---|---|---|
| Wake word | openWakeWord `hey_jarvis` (interim) → custom `hi_diane.onnx` (M7) | openwakeword pretrained; custom trained per `scripts/train_wakeword/` | frame-level ONNX, CPU |
| VAD | Silero VAD | bundled with openwakeword / torch-free ONNX | ~700 ms trailing window |
| STT | faster-whisper `small`, int8 | auto-downloaded to `models/whisper` | Indian English + `initial_prompt` name biasing |
| LLM | `qwen2.5:3b-instruct` | `ollama pull qwen2.5:3b-instruct` | streamed, warm |
| VLM | `moondream` | `ollama pull moondream` | on-demand screen description |
| TTS | Piper `en_GB-southern_english_female-low` | `models/piper/` (see below) | sentence-chunk streaming |

## Piper voice download

```bash
mkdir -p models/piper && cd models/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx.json
```

Disk budget: whisper-small ≈ 500 MB, qwen2.5:3b ≈ 2 GB, moondream ≈ 1.7 GB,
piper voice ≈ 65 MB, wake/VAD < 50 MB. Total ≈ 4.5 GB.
