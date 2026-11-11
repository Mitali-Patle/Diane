# Diane

A fully local, privacy-first, JARVIS-style voice assistant for the Linux desktop, with an
animated avatar that lives on your screen.

Say **"Hi Diane"**, speak, and she transcribes you, thinks with a local LLM, optionally looks at
your screen, runs whitelisted system actions, and answers out loud — with an animated cat (or any
avatar pack you drop in) reflecting her state and following your cursor with its eyes.

**Nothing leaves the machine.** No audio, no screenshots, no transcripts. The only network
endpoint the process is permitted to touch is `localhost:11434` (Ollama).

```
   "Hi Diane, what's on my screen?"
         │
    ┌────▼─────┐   ┌──────────┐   ┌─────────┐   ┌───────────┐   ┌─────┐   ┌─────────┐
    │ wake+VAD │──▶│   STT    │──▶│  brain  │──▶│  chunker  │──▶│ TTS │──▶│ speaker │
    │   oww    │   │ whisper  │   │ qwen2.5 │   │ sentences │   │piper│   │         │
    └──────────┘   └──────────┘   └────┬────┘   └───────────┘   └─────┘   └─────────┘
                                       │  tools ──▶ registry ──▶ executor (whitelisted argv)
                                       │  vision ─▶ mss ──▶ moondream
                                       └─ STATE_CHANGED ──▶ 🐱 avatar
```

---

## Table of contents

- [What it does](#what-it-does)
- [Status](#status)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Installation](#installation)
- [Running](#running)
- [Configuration reference](#configuration-reference)
- [Models](#models)
- [The avatar](#the-avatar)
- [Tools and the security model](#tools-and-the-security-model)
- [Wake-word training](#wake-word-training)
- [Testing and CI](#testing-and-ci)
- [Measured performance](#measured-performance)
- [Scripts](#scripts)
- [Logs and privacy](#logs-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Project documents](#project-documents)
- [Design principles](#design-principles)
- [Roadmap](#roadmap)

---

## What it does

| | |
|---|---|
| **Wake word** | Always-on, CPU-resident openWakeWord. Ships with a custom-trained `hi_diane` model; `hey_jarvis` is the pretrained fallback. |
| **Voice activity detection** | Silero VAD v5 (ONNX, torch-free) segments your utterance and ends it on ~700 ms of trailing silence. |
| **Speech to text** | faster-whisper `small` int8 on CPU, streaming partials, tuned for Indian English with contact-name biasing via `initial_prompt`. |
| **Brain** | Ollama `qwen2.5:3b-instruct`, streamed, kept warm, with a rolling in-memory context window. |
| **Tools** | `open_app`, `list_dir`, `check_disk`, `run_command`, `describe_screen`, `set_avatar` — all whitelist-validated, results fed back into the same turn. |
| **Screen understanding** | On demand only: one `mss` screenshot → `moondream` → spoken description. No timers, no continuous capture. |
| **Text to speech** | Piper, sentence-chunked, so playback starts on sentence 1 while the LLM is still writing sentence 2. |
| **Barge-in** | Talk over her and playback stops in well under 100 ms; your interruption becomes the next utterance. |
| **Avatar** | Frameless, transparent, always-on-top sprite that animates per assistant state, blinks, fidgets, tracks your cursor, and can be clicked to start listening. |
| **Always-on** | systemd `--user` units, a supervisor that contains and restarts crashed stages, and LLM warmup at login. |

---

## Status

All milestones **M0–M8 are complete** and the full loop has been validated live on real hardware.

| Milestone | What landed | Commit |
|---|---|---|
| **M0** Foundation | bus, frozen config, logging, scaffold, handbook v1.2, CI, project agents | `e83e1c5` |
| **M1** Ears | audio input thread, wake word + VAD utterance segmentation | `abd3734` |
| **M2** Transcription | streaming faster-whisper STT with contact-name biasing | `6a3bf7e` |
| **M3** Brain | orchestrator state machine, streamed Ollama chat, sentence chunker | `ac15d08` |
| **M4** Hands | whitelist tool registry, executor, append-only audit log | `cb4d514` |
| **M5** Voice | Piper streaming TTS, output thread, barge-in playback stop | `4519a23` |
| **M6** Eyes | on-demand screen understanding via moondream | `c490beb` |
| **M7** Always-on | supervisor, systemd units, warm LLM, custom "Hi Diane" wake model | `900afd1` |
| **M8** Face | avatar subsystem (window, animator, eyes, packs) + placeholder cat | `555fe6c` |
| — | live validation: real-voice wake model, AEC, barge-in tuning | `9bdb2ce` |

**124 tests pass** (112 headless + 12 offscreen avatar). Known open items — see also the
2026-11-11 audit notes in `HANDBOOK.md` DL-15 – DL-18:

- **SC2 is marginal** — end-of-speech to first spoken word measures ~3.4 s against a <3 s CPU
  budget. Tuning options are noted in [`models.md`](models.md): a smaller LLM
  (`qwen2.5:1.5b-instruct`), trimming tool schemas from non-tool turns, or accepting it on Tier A.
- **False wakes.** The `hi_diane` model still fires about once a minute on room tone and often on
  other people's voices (TV, calls). `wake.patience: 2` halves it; the real fix is retraining with
  recorded ambient audio (`scripts/train_wakeword/record_real.py ambient 300`, then `train.py`,
  then compare with `live_eval.py`).
- **Avatar art is built-in, code-drawn.** `scripts/make_placeholder_cat.py` now renders two
  anti-aliased packs (`cat` lavender tabby, `ginger` orange tabby) with breathing, blinking,
  fidgets, and per-state poses. Owner-chosen art and
  a second `axolotl` pack (the Appendix B documentation test) are still owed.
- **SC9 long-session measurement** (avatar idle CPU over a full day) has not been run yet.
- Contact names for `stt.contact_names` have not been supplied, so name biasing is currently inert.

---

## Architecture

**One process. Three concurrency domains.** In-memory `asyncio.Queue`s beat any IPC for a laptop
app, so there is no broker, no socket, no serialization.

1. **Audio-in OS thread** — the PortAudio callback does a queue-put and nothing else. 16 kHz mono
   int16, blocksize 512, into a thread-safe `janus` queue. If the asyncio side stalls, frames are
   dropped and counted rather than growing the queue without bound.
2. **Unified Qt + asyncio event loop** — PySide6 integrated via **qasync**, so one loop runs the
   wake/VAD task, STT, orchestrator, chunker, TTS worker, tool executor, the interrupt bus, *and*
   the avatar's paint and timer events. Every blocking model call (whisper decode, Piper synthesis,
   screenshot grab) is pushed to a thread executor so the loop stays live.
3. **Audio-out OS thread** — the PortAudio callback pulls PCM out of a lock-guarded buffer.
   `flush()` empties that buffer in O(1), which is what makes barge-in stop audible sound inside
   one 512-frame block.

### State machine

The orchestrator is the **sole owner** of assistant state. Every transition publishes
`StateChanged` on the bus.

```
        wake word / AVATAR_ACTIVATE          VAD end            first token
 IDLE ─────────────────────────────▶ LISTENING ────────▶ THINKING ──────────▶ SPEAKING
   ▲                                     ▲                                      │
   │                                     └──────── barge-in (voice only) ───────┤
   └────────────────── complete / cancel ───────────────────────────────────────┘

 any stage failure ──▶ ERROR (spoken + rendered) ──▶ IDLE
```

Barge-in has a subtlety worth knowing before you touch it: on voice barge-in the wake stage
publishes `Cancel` **and** synchronously calls the orchestrator's `on_wake` hook to start capture.
So `on_wake` cancels the in-flight turn itself — `Cancel` handling can never be guarded on
`SPEAKING` state alone, because the state has already moved to `LISTENING` by then.

### The bus

`jarvis/bus.py` holds the state enum, every shared payload, and an in-process pub/sub.

All payloads are `@dataclass(frozen=True, slots=True)` — no consumer can mutate shared state:
`AudioFrame`, `PartialTranscript`, `FinalTranscript`, `Token`, `Phrase`, `PcmChunk`,
`ToolCall`/`ToolResult`, `ScreenQuery`/`ScreenDescription`, `AvatarCommand`, `AvatarStatus`.

Events: `Cancel`, `StateChanged(new_state)`, `AvatarActivate`.

Subscribers get an unbounded queue by default. The avatar subscribes with `latest_wins=True` — a
depth-1 mailbox where a new event replaces any unread one, because **animation must never queue up
behind reality**. If the animator is mid-frame when a new state arrives, it drops the stale target
and jumps to the newest.

### Module boundaries

A module may import from `bus.py`, `config.py`, and its own package. Everything else goes through
the queue and event interfaces. Exclusive responsibilities are enforced by convention and audited
by the `handbook-auditor` agent:

- Only `brain/orchestrator.py` mutates assistant state.
- Only `actions/executor.py` spawns command subprocesses.
- Only `perception/wake_vad.py` publishes barge-in `Cancel`.
- Only `avatar/window.py` publishes `AVATAR_ACTIVATE`.
- Only `avatar/packs.py` reads pack files from disk.
- **Qt is imported only inside `jarvis/avatar/`** — that is what keeps the whole pipeline
  headless-testable.

---

## Repository layout

```
Diane/
├── jarvis/
│   ├── main.py                 # composition root: qasync loop, threads, tasks, supervisor
│   ├── bus.py                  # interrupt bus, shared payloads, State enum
│   ├── config.py               # frozen (deeply immutable) config loader
│   ├── logs.py                 # app log + append-only audit log
│   ├── audio/
│   │   ├── input_thread.py     # PortAudio in → janus queue (put-only callback)
│   │   └── output_thread.py    # PortAudio out ← lock-guarded buffer, O(1) flush
│   ├── perception/
│   │   ├── wake_vad.py         # openWakeWord + Silero VAD, utterance segmentation, barge-in
│   │   ├── stt.py              # faster-whisper streaming transcription
│   │   └── vision.py           # on-demand mss screenshot → moondream
│   ├── brain/
│   │   ├── orchestrator.py     # state machine, rolling context, streamed Ollama, tool rounds
│   │   └── chunker.py          # token stream → sentence-level phrases
│   ├── voice/
│   │   └── tts.py              # Piper synthesis → PcmChunk stream
│   ├── actions/
│   │   ├── registry.py         # the one reviewable whitelist; ToolCall → ResolvedAction
│   │   ├── executor.py         # the only subprocess spawner; audits every execution
│   │   └── __init__.py         # Actions facade (resolve → execute / route to vision)
│   └── avatar/
│       ├── window.py           # frameless QWidget: paint, drag, click, context menu
│       ├── animator.py         # state → animation mapping, frame stepping, blink/fidget
│       ├── eyes.py             # cursor clamping + lerp smoothing (pure math, no Qt)
│       └── packs.py            # pack discovery, manifest validation, sprite loading, hot-swap
├── avatars/
│   ├── pixelcat/               # DEFAULT: "Mochi", original pixel-art cat (+ -grey, -peach coats)
│   ├── bolt/                   # original retro robot
│   ├── cat/                    # lavender tabby (smooth art)
│   └── ginger/                 # orange tabby (smooth art)
├── scripts/                    # smoke tests, latency measurement, service install, wake training
├── tests/                      # mirrors the package structure
├── models/                     # gitignored model artifacts (~4.5 GB when populated)
├── logs/                       # gitignored app.log + audit.log
├── config.yaml                 # single source of runtime config
├── HANDBOOK.md                 # the binding spec — read this before changing anything
├── models.md                   # model table, download commands, measured latencies
├── setup.md                    # system packages, Ollama, venv, AEC setup
└── .github/workflows/ci.yaml   # headless job + offscreen avatar job
```

`avatar_state.json` (gitignored) holds machine-written ephemera: `active_pack`, `window_x`,
`window_y`, `eyes_enabled`, `visible`. It is deliberately separate from `config.yaml` — config is
frozen at startup and human-authored; window position is not.

---

## Installation

Target platform: **Ubuntu on X11**, CPU-only ("Tier A": a mid-range laptop CPU, no GPU needed, 16 GB RAM). Python 3.12.

### 1. System packages

```bash
sudo apt install portaudio19-dev python3.12-venv
# Qt runtime for the avatar:
sudo apt install libxcb-cursor0 libegl1 libxkbcommon-x11-0
```

### 2. Ollama

The only permitted network endpoint is `localhost:11434`.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct
ollama pull moondream
```

### 3. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[avatar,dev]"

# openwakeword 0.6.0 declares tflite-runtime, which has no Python 3.12 wheels.
# We use its ONNX path instead, so install it without deps and add back what we need:
.venv/bin/pip install --no-deps openwakeword==0.6.0
.venv/bin/pip install tqdm scipy requests
```

### 4. Model artifacts

```bash
# wake/VAD base models
.venv/bin/python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

# Piper voice
mkdir -p models/piper && cd models/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx.json
```

`models/silero_vad.onnx` is also required — see [`models.md`](models.md). faster-whisper downloads
itself into `models/whisper/` on first run and is never fetched again afterwards
(`local_files_only=True` once the snapshot exists).

### 5. Echo cancellation — required for barge-in

Without AEC the assistant hears her own TTS through the speakers and cancels herself.

```bash
pactl load-module module-echo-cancel aec_method=webrtc \
    source_name=diane_ec_source sink_name=diane_ec_sink
pactl set-default-source diane_ec_source && pactl set-default-sink diane_ec_sink
```

For a permanent setup, put the equivalent in
`~/.config/pipewire/pipewire-pulse.conf.d/99-diane-echo-cancel.conf`.

Also check your mic gain: `pactl get-source-volume @DEFAULT_SOURCE@`. Laptop mics often ship
at a very low gain, which is effectively inaudible to the wake model — around 70% is right.

---

## Running

```bash
.venv/bin/python -m jarvis.main
```

As a background service (installs a user-level Ollama unit too):

```bash
bash scripts/install_service.sh
systemctl --user start diane-ollama diane
journalctl --user -u diane -f
```

Then just talk: say **"Hi Diane"**, wait for the avatar to perk up, and speak. Or left-click the
avatar, which is exactly equivalent to the wake word.

To run completely headless, set `avatar.enabled: false` in `config.yaml`. The pipeline behaves
identically — the avatar is a view, never a dependency. If PySide6 or qasync is missing, the app
logs a warning and runs headless on its own.

---

## Configuration reference

Everything lives in `config.yaml`, and it is **frozen at startup**: `config.get()` returns a deeply
immutable mapping, so mutating it anywhere raises. Changing anything requires a restart — with one
sanctioned exception, `avatar.active_pack`, which is only the startup default and whose runtime
value lives in `avatar_state.json`.

### `audio`
| Key | Default | Meaning |
|---|---|---|
| `sample_rate` | `16000` | Mono int16 throughout the pipeline. |
| `blocksize` | `512` | Frames per PortAudio callback. |
| `input_device` / `output_device` | `null` | `null` = system default. |

### `wake`
| Key | Default | Meaning |
|---|---|---|
| `model` | `hi_diane` | Resolves to `models/<name>.onnx` if present, else a pretrained openWakeWord model. Set to `hey_jarvis` if the custom model misbehaves. |
| `threshold` | `0.6` | Wake score threshold. |
| `vad_threshold` | `0.5` | Silero speech probability threshold. |
| `end_of_utterance_ms` | `700` | Trailing silence that ends an utterance. |

### `stt`
| Key | Default | Meaning |
|---|---|---|
| `model` | `small` | faster-whisper size. |
| `compute_type` | `int8` | CPU quantization. |
| `language` | `en` | |
| `contact_names` | `[]` | Names biased into whisper's `initial_prompt` — the fix for Indian names being mangled. |

### `llm`
| Key | Default | Meaning |
|---|---|---|
| `host` | `http://localhost:11434` | The one sanctioned external contract. |
| `model` | `qwen2.5:3b-instruct` | |
| `context_turns` | `10` | Rolling window; older exchanges are trimmed, system prompt always kept. |
| `keep_alive` | `2h` | Keeps the model resident between turns. |

### `vlm`
| Key | Default | Meaning |
|---|---|---|
| `model` | `moondream` | On-demand screen description. |

### `tts`
| Key | Default | Meaning |
|---|---|---|
| `voice` | `en_GB-southern_english_female-low` | Piper voice basename. |
| `voices_dir` | `models/piper` | |

### `tools`
| Key | Default | Meaning |
|---|---|---|
| `app_whitelist` | firefox, nautilus, gnome-terminal, gedit, gnome-calculator | Apps `open_app` may launch. |
| `command_whitelist` | ls, df, free, date, uptime, cat, whoami | Names `run_command` may run (intersected with the fixed-argv table). |
| `dir_roots` | `["~"]` | `list_dir` may only browse inside these. |
| `timeout_s` | `10` | Subprocess timeout. |

### `logging`
| Key | Default | Meaning |
|---|---|---|
| `app_log` | `logs/app.log` | Stage lifecycle, errors, avatar events. |
| `audit_log` | `logs/audit.log` | Append-only, one record per executed command. |
| `level` | `INFO` | |

### `avatar`
| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` runs the pipeline headless, identically. |
| `packs_dir` | `avatars/` | |
| `active_pack` | `cat` | Startup default only; runtime value persists to `avatar_state.json`. |
| `scale` | `1.0` | Clamped to 0.5–2.0. |
| `eye_tracking` | `true` | |
| `eye_poll_hz` | `30` | Capped at 30. |
| `fps_cap` | `30` | |
| `click_to_activate` | `true` | Left-click = wake word. |
| `click_through` | `false` | Makes the window transparent to input (activation becomes voice-only). |
| `blink` | `{min_s: 3, max_s: 8}` | Jittered blink timer, idle only. |
| `fidget` | `{min_s: 20, max_s: 60}` | Jittered fidget timer, idle only. |

---

## Models

| Stage | Model | Where it comes from | Size |
|---|---|---|---|
| Wake word | custom `hi_diane.onnx` (fallback `hey_jarvis`) | trained by `scripts/train_wakeword/` | 788 KB |
| VAD | Silero VAD v5 | `models/silero_vad.onnx` | 2.3 MB |
| STT | faster-whisper `small`, int8 | auto-downloaded to `models/whisper/` | ~464 MB |
| LLM | `qwen2.5:3b-instruct` | `ollama pull` | ~2 GB |
| VLM | `moondream` | `ollama pull` | ~1.7 GB |
| TTS | Piper `en_GB-southern_english_female-low` | `models/piper/` | ~65 MB |

Total on-disk budget ≈ 4.5 GB. All of `models/` is gitignored.

Two implementation details that are easy to get wrong and are worth knowing:

- **Silero v5 needs context.** Each 512-sample chunk must be prefixed with the last 64 samples of
  the previous chunk. The official wrapper does this internally; feeding bare chunks silently
  degrades speech probabilities to near zero on real speech. `SileroVad` handles it.
- **The custom wake model reuses openWakeWord's streaming feature frontend** (melspec + embedding)
  and puts a small ONNX MLP head over a 16-frame embedding window. Train and serve use the same
  frontend, so there is essentially no train/serve skew.

---

## The avatar

The avatar is a **passive view plus exactly one input affordance**. It renders from
`STATE_CHANGED`, it emits `AVATAR_ACTIVATE` on click, and that is the entirety of its power. It
never mutates orchestrator state, calls tools, reads transcripts, or captures the screen — the
render layer is the least security-reviewed code in the system, and write-blindness means a
compromised pack cannot escalate.

**Rendering** is layered 2D sprite compositing, back to front: the body layer (current animation
frame), the optional eyes layer (a socket-anchored pupil per eye, offset by the clamped, lerped
cursor vector), and optional overlays (blink frames, fidgets, on their own timers). Layering rather
than pre-baking eye positions is what makes eye tracking a manifest feature instead of a per-pack
art burden.

**Frame stepping** uses one `QTimer` per active animation at the manifest's fps (capped at 30). A
static single-frame animation stops its timer entirely. Frames are decoded once at pack load and
the cache is dropped on pack switch.

**Eye tracking** polls `QCursor.pos()` at up to 30 Hz — polling, never a global input hook, which
is both privacy-inert and portable. The vector from socket center to cursor is clamped to
`socket_radius`, then `pupil += (target - pupil) * 0.25` per tick for organic lag. On Wayland this
degrades to window-relative tracking, logged once at startup.

**Interaction:** left-click activates listening (equivalent to the wake word, and never barge-in —
clicking a speaking avatar does nothing). Drag to move; position is persisted and clamped back into
visible screen geometry at startup. Right-click opens a menu: switch to any discovered pack, toggle
eye tracking, hide the avatar, quit Diane.

**Failure containment:** every Qt slot wraps its body in try/except, logs, and emits
`AvatarStatus(error=…)` — which the assistant then *speaks*, because you are the operator. Three
crashes in 60 seconds disables the stage for the session with a spoken notice. If no pack loads at
all, a built-in code-drawn **dot avatar** takes over: a pulsing circle, color-coded per state,
needing zero assets, so state visibility survives any pack failure.

### Adding your own avatar pack

Adding an avatar is a data operation. If a step here ever needs a Python change, the pack format is
broken and that is a bug, not a workaround.

```
avatars/
└── axolotl/                 # pack name = directory name, [a-z0-9_-]
    ├── avatar.yaml          # manifest (required)
    ├── LICENSE              # art license/credits (required — non-negotiable)
    ├── idle/  000.png 001.png
    ├── listening/  thinking/  speaking/
    ├── error/               # optional
    ├── blink/               # optional overlay
    ├── fidget_01/           # optional, any number
    └── eyes/pupil.png       # optional; presence enables eye tracking
```

```yaml
name: "Axolotl"
version: 1
author: "owner"
frame_size: [128, 128]       # must match every frame exactly
anchor: bottom-center        # bottom-center | center | top-center

states:
  idle:      {anim: idle,      fps: 8,  loop: true}
  listening: {anim: listening, fps: 12, loop: true}
  thinking:  {anim: thinking,  fps: 10, loop: true}
  speaking:  {anim: speaking,  fps: 12, loop: true}
  error:     {anim: error,     fps: 6,  loop: false}   # holds last frame

fallbacks:                   # used when a state or directory is missing
  speaking: idle
  thinking: idle
  listening: idle
  error: idle

overlays:
  blink:   {anim: blink, fps: 16, only_in: [idle]}
  fidgets: [fidget_01]       # picked randomly on the fidget timer

eyes:                        # omit the whole block for no eye tracking
  pupil: eyes/pupil.png      # must resolve inside the pack directory
  sockets:
    - {center: [46, 52], radius: 6}
    - {center: [82, 52], radius: 6}
  hide_in: [blink, error]
```

Steps: copy an existing pack, replace the PNGs, edit the manifest, add a LICENSE, then restart or
pick the pack from the right-click menu — or just say *"switch to the axolotl"*. A minimum viable
pack is one `idle/` directory with a single frame and fallbacks routing every state to `idle`.

Validation is enforced, not advisory: unknown top-level keys are rejected, every path must resolve
inside the pack directory (no `../`), fps is clamped to 1–30, every `states.*.anim` and
`fallbacks.*` target must exist as a real frame directory, socket centers must lie inside
`frame_size`, images are capped at 2048×2048 and 5 MB, the whole pack at 40 MB, frames must match
the declared `frame_size`, and symlinks pointing outside the pack are rejected at load. An invalid
pack is rejected with a spoken error and **the previous pack stays active**.

Design tips: idle should breathe (a 2–4 frame subtle loop beats a static image); listening must be
instantly distinct from idle, because that animation *is* the feedback the user relies on; keep
pupils small relative to socket radius, since large pupils with small travel look dead; make error
endearing rather than alarming — it is a companion, and errors are spoken anyway.

---

## Tools and the security model

The model **proposes**; the code **disposes**. The LLM never receives raw shell, and never supplies
a string that reaches one.

| Tool | Argument | What actually runs |
|---|---|---|
| `open_app` | `name` (enum from whitelist) | `exec(name)` detached, after a `shutil.which` check |
| `list_dir` | `path` | `ls -la <resolved>`, containment-checked against `dir_roots` |
| `check_disk` | — | `df -h /` (fixed) |
| `run_command` | `name` (enum) | A fixed argv from a hardcoded table: `ls -la`, `df -h`, `free -h`, `date`, `uptime`, `whoami` |
| `describe_screen` | optional `question` | One screenshot → moondream. No subprocess. |
| `set_avatar` | `name` | Publishes `AvatarCommand("set_pack", name)` after validating against the discovered pack list |

The layered model, in order:

1. **Whitelist by construction.** The registry maps a *name* to a fixed argv. The model picks from
   an enum; it never composes arguments. Unknown anything is rejected, and the rejection is
   returned as the tool output so Diane speaks the refusal instead of silently failing.
2. **Path containment.** `list_dir` resolves the request and requires it to be inside
   `tools.dir_roots`. `~/../etc` resolves out and is refused with "I can only browse folders inside
   your home directory."
3. **Non-privileged execution.** `asyncio.create_subprocess_exec` with an argv list —
   `shell=False` by construction — as the session user, with a timeout.
4. **Cancellation safety.** A cancelled turn kills the subprocess rather than leaking it.
5. **Append-only audit.** Every execution is logged *before* and *after* it runs, so a crash
   mid-command still leaves the attempt on record: `EXEC`/`DONE`/`TIMEOUT`/`CANCELLED` with tool,
   resolved argv, exit code, and turn id. The audit handler opens in append mode and nothing in the
   codebase rotates or rewrites it.
6. **Destructive actions** carry `destructive: true` in the registry and require spoken
   confirmation against the *specific resolved command*. The v1 builtin set contains none.
7. **Tool results feed back into the same turn** (up to 3 rounds), so "check my disk" produces one
   spoken answer, not two.

`set_avatar` is a tool rather than intent-parsing on purpose: *all* actions stay in the one
reviewable registry, even cosmetic ones. Because it spawns no subprocess, it goes to the app log
rather than the audit log.

---

## Wake-word training

The custom `hi_diane` model was trained entirely locally with synthetic data, because
GB-scale negative corpora were impractical to download on this connection.

```bash
# 1. Generate synthetic samples: LibriTTS speakers say wake-phrase variants at
#    varied speeds, with gain/noise/placement augmentation. Negatives use the same
#    speakers and augmentation so the classifier can't cheat on channel differences.
.venv/bin/python scripts/train_wakeword/generate_samples.py

# 2. Record real-mic clips for domain adaptation and honest validation.
.venv/bin/python scripts/train_wakeword/record_real.py positive 60
.venv/bin/python scripts/train_wakeword/record_real.py negative 45

# 3. Train: wav → oww embeddings → 16-frame windows → MLP → ONNX.
.venv/bin/python scripts/train_wakeword/train.py
```

The negative set deliberately includes the phonetically adjacent traps — *"Hi Dana"*, *"Hi Ryan"*,
*"Hi Dan"*, *"My dear Anne"*, *"High dive"*, *"Bye Diane"*, *"Hindi and English"* — alongside
generic speech and noise. Real-mic windows are oversampled 12× against synthetic ones, and two real
positive clips are held out entirely; their best window score is the number that actually matters.

Holdout results: **false-accept 0.3%, false-reject 1.9% @ 0.5**.

Training additionally needs `scikit-learn` and `skl2onnx`, which are not in `pyproject.toml` —
install them only if you intend to retrain.

If the custom model ever misbehaves in your room, set `wake.model: hey_jarvis` in `config.yaml`. A
configured custom model that has gone missing from disk falls back to `hey_jarvis` automatically
and logs it, rather than crash-looping the service.

---

## Testing and CI

```bash
.venv/bin/python -m pytest tests/ -q --ignore=tests/avatar  # 112 headless tests
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/avatar -q
.venv/bin/ruff check jarvis tests
```

The suite runs in about a second because every model is injected behind a Protocol — fake LLMs,
fake whisper models, fake Piper voices, scripted wake scorers. No test downloads or loads a real
model.

| Area | What is covered |
|---|---|
| `test_bus.py` | fan-out, latest-wins mailbox dropping stale events, unsubscribe, payload immutability |
| `test_config.py` | expected sections, deep immutability, audio contract |
| `perception/test_wake_vad.py` | pre-roll capture, 80 ms chunking, external activation, pauses mid-utterance, give-up with no speech, refractory + scorer reset blocking re-wake, max-length cutoff, barge-in fires on sustained speech, brief noise does *not*, no barge-in while idle, one Cancel per episode |
| `perception/test_stt.py` | partials accumulate then final, empty audio still finalizes, contact names reach `initial_prompt` |
| `perception/test_vision.py` | one capture per describe, custom question routing, rejection without a VLM, no continuous capture at construction |
| `brain/test_orchestrator.py` | happy-path state sequence, context trimming, barge-in mid-stream, `AVATAR_ACTIVATE` only in IDLE, LLM failure → ERROR → IDLE, the `on_wake` race, external-playback SPEAKING persistence |
| `brain/test_chunker.py` | sentence boundaries, tail flush, short-fragment hold, run-on force-break |
| `voice/test_tts.py` | PCM chunk streaming, empty synthesis |
| `actions/test_registry.py` | the whole whitelist matrix — unknown tools, non-whitelisted commands and apps, fixed argv mapping, traversal containment, unknown pack rejection, spec enums matching the whitelists |
| `actions/test_executor.py` | execution + audit, timeout kill + audit, cancellation kill, avatar commands bypassing subprocess *and* audit, facade rejection never executing, append-only handler |
| `avatar_headless/` | manifest validation matrix (traversal, oversize, missing state, unknown keys, missing LICENSE, minimum viable pack), animator mapping and fallback chain, loop vs hold-last-frame, blink gating, static-frame timer stop, eye clamping and lerp math |
| `avatar/test_window.py` | pack load and decode, state event → correct animation, **event→frame selection under 100 ms**, hot-swap under a running timer, invalid pack keeps previous, dot fallback paints without assets, click publishes activate, drag does not, latest-wins consumption, slot containment error counting, symlinked frame outside pack rejected |

CI runs two jobs: a **headless** job that lints and runs everything except `tests/avatar` with no
audio or ML dependencies installed (proving the pipeline does not depend on Qt), and an
**avatar-offscreen** job with `QT_QPA_PLATFORM=offscreen`.

### Project agents

`.claude/agents/` holds three subagents used during development:

- **handbook-auditor** — read-only reviewer that audits a diff against the handbook's P1–P10
  principles, NFR budgets, and module-boundary rules. Run before every commit.
- **milestone-verifier** — runs the suite and a milestone's exit criteria, reporting pass/fail with
  evidence.
- **module-builder** — implementation agent that knows the import rules and event contracts.

---

## Measured performance

Measured on Tier A (a mid-range laptop CPU, CPU-only), 2026-07-06:

| Span | Measured | Budget | |
|---|---|---|---|
| Wake scorer (`hey_jarvis` / `hi_diane`) | 1.4 / 1.3 ms per 80 ms chunk | realtime | ✅ |
| Silero VAD | 0.08 ms per 32 ms chunk | realtime | ✅ |
| Speech-end → first phrase (warm LLM, tools on) | 3.1 s | <1.5 s | ❌ over |
| First phrase → first audio (Piper) | ~0.3 s | <0.8 s | ✅ |
| Screen description (moondream, warm) | 11.5 s | <15 s | ✅ |
| **End-of-speech → first spoken word** | ~3.4 s | <3 s | ⚠️ marginal |
| State event → avatar frame selection | <1 ms | <100 ms | ✅ |
| Avatar idle (cat pack, eyes on, X11) | 2.9% of one core, 62 MB RSS | <3% / <80 MB | ✅ |
| Pack hot-swap | <0.1 s | <1 s | ✅ |
| Barge-in stop | one 512-frame block | <100 ms | ✅ |

Re-measure with `.venv/bin/python scripts/measure_latency.py`.

The governing metric is **perceived** latency — "end of my speech → first spoken word" — which is
why streaming, sentence chunking, and concurrent tracks all exist. The avatar's ears perking within
100 ms is itself a perceived-latency win.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/mic_wake_test.py [model]` | Live mic → wake word → utterance segmentation. Prints durations and timing. Pass `hi_diane` to exercise the custom model. |
| `scripts/mic_stt_test.py` | Live mic → wake → streaming transcription, printing partials and finals. |
| `scripts/text_chat_test.py` | Typed text in → streamed LLM response + state events out. No mic needed; requires Ollama. |
| `scripts/measure_latency.py` | Scripted latency check against the budgets. |
| `scripts/install_service.sh` | Installs `diane.service` and `diane-ollama.service` as systemd `--user` units. |
| `scripts/make_pixel_cat.py` | Regenerates the `pixelcat` packs (Mochi, pixel art: asleep when idle, wakes when active). |
| `scripts/make_robot_pack.py` | Regenerates the `bolt` robot pack. |
| `scripts/make_placeholder_cat.py` | Regenerates the built-in `cat` and `ginger` packs (4× supersampled vector drawing). |
| `scripts/train_wakeword/live_eval.py` | Counts false wakes per model on live room audio (memory only). |
| `scripts/train_wakeword/*.py` | Synthetic sample generation, real-mic recording, and wake model training. |

---

## Logs and privacy

Two streams, both under `logs/` and both gitignored:

- **`app.log`** — stage lifecycle, errors, avatar events, state transitions with turn ids.
- **`audit.log`** — append-only, one record per executed command: timestamp, tool, resolved argv,
  exit code.

**Never logged, at any level:** transcripts, screen content, cursor coordinates, window position.
Transcripts are never written to disk at all — they exist only as bus payloads and in the
orchestrator's in-memory rolling context, which is discarded on exit. There is no cross-session
memory in v1.

The systemd unit sets `StandardOutput=null` so nothing leaks to the journal; stage logs go to the
app log.

---

## Troubleshooting

**The wake word never fires.** Check mic gain first — `pactl get-source-volume @DEFAULT_SOURCE@`;
anything under ~50% is likely too quiet for the scorer. Then run
`.venv/bin/python scripts/mic_wake_test.py hi_diane` and watch the scores. If the custom model is
flaky in your acoustic environment, set `wake.model: hey_jarvis`.

**She interrupts herself mid-sentence.** Echo cancellation is not active — she is hearing her own
TTS. See [step 5](#5-echo-cancellation--required-for-barge-in). There is also a 0.5 s onset grace
after SPEAKING begins (the webrtc AEC needs a moment to converge) and a requirement for ~256 ms of
sustained speech before barge-in fires, so a pop or a click cannot cancel a turn.

**Barge-in never triggers.** The opposite problem: AEC may be suppressing you too. Check that
`diane_ec_source` is actually the default source.

**Everything is slow on the first turn.** The LLM warmup task retries for 60 s at startup; if
Ollama was not up yet, turn 1 pays the model-load cost. `journalctl --user -u diane` will show it.

**The avatar does not appear.** Confirm `avatar.enabled: true`, that PySide6 and qasync are
installed (`pip install -e ".[avatar]"`), and that the Qt runtime packages from step 1 are present.
A failed avatar start never takes down the pipeline — it logs and continues headless.

**A pack will not load.** Diane speaks the first validation error out loud, and it is also in
`app.log`. The previous pack stays active; if none ever loaded, you get the dot.

**The avatar drifted off-screen.** Delete `avatar_state.json`; position is clamped into visible
geometry at startup, but deleting the file resets everything.

**`openwakeword` install fails on Python 3.12.** That is the tflite-runtime wheel. Use the
`--no-deps` path in [step 3](#3-python-environment).

---

## Project documents

| Document | What it is |
|---|---|
| **[`HANDBOOK.md`](HANDBOOK.md)** | The binding source of truth: vision, principles P1–P10, success criteria SC1–SC10, functional and non-functional requirements, architecture, security model, milestones, budgets, decision log DL-1–DL-14, open questions, and the full avatar pack authoring guide (Appendix B). **Read it before changing anything.** |
| [`setup.md`](setup.md) | System packages, Ollama, venv, AEC configuration. |
| [`models.md`](models.md) | Model table, download commands, disk budget, measured latencies. |
| `HANDBOOK.md.pdf` | The original v1.1 PDF the handbook was reconstructed from. |

The handbook is authoritative and versioned: any interface addition, new principle, or milestone
change bumps its version and updates it **in the same commit as the code**. Decision-required
blocks are resolved only by the owner, and resolutions are recorded in the decision log.

---

## Design principles

These are non-negotiable, not aspirations. The `handbook-auditor` agent exists to check diffs
against them.

| | |
|---|---|
| **P1** | No raw shell for the model. All actions go through the tool registry. |
| **P2** | Whitelist, never blacklist. |
| **P3** | Destructive actions require confirmation against the specific *resolved* command. |
| **P4** | Non-privileged execution. |
| **P5** | Every long await is cancellable — shutdown must never hang. |
| **P6** | Audio callbacks do queue-puts only. |
| **P7** | Append-only audit log for every executed command. |
| **P8** | Screen capture is on-demand only, never continuous. |
| **P9** | The avatar is a passive view plus one input affordance. It never mutates state, calls tools, reads transcripts, or captures the screen. |
| **P10** | Avatar packs are data, never code. Images and a declarative manifest. No Python, no scripts, no eval of any manifest field. |

And the philosophy underneath them:

1. **Perceived latency beats actual latency.** The metric is "end of my speech → first spoken word".
2. **Local-first, always.** Nothing leaves the machine.
3. **Single process until proven otherwise.** In-memory queues beat any IPC for a laptop app.
4. **The model proposes; the code disposes.**
5. **Degrade gracefully across hardware.** The hardware tier selects models only — never feature
   behavior.

### Accepted tradeoffs

Small local models are less capable than cloud ones (privacy outranks). Whitelist rigidity is
inconvenient (safety outranks). A single process couples stages (explicit split triggers exist).
whisper-small on CPU limits name accuracy headroom (biasing compensates). No cross-session memory,
English output only in v1. qasync couples GUI and pipeline into one loop (measured by latency CI,
with a documented escape hatch to move the avatar behind an event socket). Sprite-sheet 2D only,
no rigged animation (keeps packs pure data). 30 Hz cursor polling can step under fast motion (lerp
hides most of it).

---

## Roadmap

Explicitly out of scope for v1, ordered by interest:

1. GUI control — acting on what's on screen, not just describing it
2. Cloud fallback brain
3. Cross-session memory
4. Hindi and code-switched responses
5. Custom cloned voice
6. Avatar life: mic-amplitude ear twitch, speech-viseme mouth flaps, dragging physics,
   edge-walking, tool-result reactions, seasonal outfits
7. Community pack format v2 with rigged animation — requires revisiting the "packs are data"
   principle with a sandboxed runtime, and should not be attempted casually

---

*Diane is a personal project, not a product. It is not multi-user, not a service, and not an
autonomous agent acting without oversight. Privacy and responsiveness outrank raw intelligence.*
