# Model Table — Tier A (CPU-only, D-1 resolved 2026-07-06)

Hardware: i7-1360P, Iris Xe (no CUDA), 16 GB RAM. Tier selects models only —
never feature behavior (§8).

| Stage | Model | Artifact / source | Notes |
|---|---|---|---|
| Wake word | custom `hi_diane.onnx` (trained M7; `hey_jarvis` pretrained fallback) | `scripts/train_wakeword/` — synthetic LibriTTS samples, oww embeddings + MLP; holdout FA 0.3% / FR 1.9% @0.5 | frame-level ONNX, CPU, 1.3 ms/chunk |
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

## Measured latencies (M7, 2026-07-06, Tier A i7-1360P)

| Span | Measured | Budget (§22) | Status |
|---|---|---|---|
| Wake scorer (hey_jarvis / hi_diane) | 1.4 / 1.3 ms per 80 ms chunk | realtime | ✓ |
| Silero VAD | 0.08 ms per 32 ms chunk | realtime | ✓ |
| Speech-end → first phrase (warm qwen2.5:3b, tools enabled) | 3.1 s | <1.5 s token | ✗ over |
| First phrase → first audio (Piper) | ~0.3 s | <0.8 s | ✓ |
| SC7 screen description (moondream, warm) | 11.5 s | <15 s CPU | ✓ |
| SC2 end-of-speech → first spoken word | ~3.4 s | <3 s CPU | ✗ marginal |
| SC8 state event → frame selection | <1 ms (offscreen test asserts <100 ms) | <100 ms | ✓ |
| SC9 avatar idle (cat pack, eyes on, X11) | 2.9% of one core, 62 MB RSS | <3% / <80 MB | ✓ |
| SC10 pack hot-swap | <0.1 s | <1 s | ✓ |

SC2 tuning options (open): smaller LLM (qwen2.5:1.5b-instruct), trim tool
schemas from the prompt for non-tool turns, or accept ~3.5 s on Tier A.
