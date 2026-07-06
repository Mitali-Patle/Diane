# JARVIS-LOCAL — Execution Handbook v1.2 (repo as-built)

**Provenance.** v1.1 arrived as a PDF; its v1.0 base (M1–M7 details, model table, DL-1–DL-6, T1–T6, D-1–D-5) was lost. This v1.2 is the v1.1 content plus a good-faith reconstruction of the referenced v1.0 material, marked *(reconstructed)*. Per §29 and the v1.1 closing line, **this repo copy is authoritative from the first commit**. The owner (Mitali) approved the reconstruction approach on 2026-07-06.

**Purpose.** This is the project's source of truth. Any model or engineer continuing this project must read this document first and treat it as binding unless a Decision Log entry supersedes it. Where the owner never settled a choice, a ⚠ DECISION REQUIRED block exists — do not silently resolve these; surface them to the owner.

---

## 1. Vision

A fully local, privacy-first, Jarvis-style voice assistant ("**Diane**") running on the owner's personal laptop. It listens for a wake word (**"Hi Diane"**), transcribes speech (Indian English, including Indian names), reasons with a local LLM, optionally looks at the screen, executes safe system actions (shell/apps/files), and speaks responses with low perceived latency via end-to-end streaming.

The assistant has a visible presence — a small animated avatar (a cat, an axolotl, or any pack the owner installs) that sits on the desktop, reflects assistant state (idle/listening/thinking/speaking/error), tracks the mouse cursor with its eyes, and can be clicked to activate listening. The avatar is a *view* of the assistant, never a second brain.

**NOT:** a cloud service, multi-user, a product to ship, or an autonomous agent acting without oversight. Privacy and responsiveness outrank raw intelligence. The assistant must remain fully functional with `avatar.enabled: false`.

## 2. Design Philosophy

1. **Perceived latency > actual latency.** The metric is "end of my speech → first spoken word". Streaming, sentence chunking, concurrent tracks all serve this. Avatar corollary: "listening" ears perking within 100 ms is itself a perceived-latency win.
2. **Local-first, always.** No audio, screenshots, or transcripts leave the machine. Cursor position is consumed in-memory by the renderer and discarded — never logged or transmitted.
3. **Single process until proven otherwise.** In-memory `asyncio.Queue`s beat any IPC. The avatar joins the same process via a Qt/asyncio shared loop (qasync, §7). Services/ZeroMQ only when a §27 trigger fires.
4. **The model proposes; the code disposes.** The LLM never has raw shell. The avatar has *no* channel to the LLM or tool executor.
5. **Degrade gracefully across hardware.** Same architecture on CPU-only or 12GB+ GPU; only model selection changes (§8).

## 3. Non-negotiable Principles

- **P1** — No raw shell for the model. All actions via the tool registry.
- **P2** — Whitelist, never blacklist.
- **P3** — Destructive actions require confirmation against the specific resolved command.
- **P4** — Non-privileged execution.
- **P5** — Every long await is cancellable (incl. avatar animation timers; shutdown must never hang).
- **P6** — Audio callbacks do queue-puts only.
- **P7** — Append-only audit log for every executed command.
- **P8** — Screen capture is on-demand only, never continuous.
- **P9** — The avatar is a passive view plus one input affordance: render from `STATE_CHANGED`, emit `AVATAR_ACTIVATE` on click. It never mutates orchestrator state, calls tools, reads transcripts, or captures the screen. (The render layer is the least security-reviewed code in the system; write-blind means a compromised pack cannot escalate.)
- **P10** — Avatar packs are data, never code. Images + declarative manifest. No Python, no scripts, no eval of any manifest field.

## 4. Success Criteria

- **SC1**: Wake word → assistant listening: < 300 ms perceived.
- **SC2**: End-of-speech → first spoken word: < 1.5 s GPU tier, < 3 s CPU tier.
- **SC3**: Barge-in silences playback in < 100 ms.
- **SC4**: Zero commands executed outside the whitelist across the audit log's lifetime.
- **SC5**: Indian names transcribed correctly ≥ 90% (self-recorded test set, §23).
- **SC6**: Runs a full day session without memory growth or zombie processes (incl. render loop: no unbounded pixmap/frame caching).
- **SC7**: "What's on my screen" useful description in < 5 s (GPU) / < 15 s (CPU).
- **SC8**: Avatar reflects a state transition within 100 ms of the `STATE_CHANGED` event.
- **SC9**: Avatar idle rendering uses < 3% of one CPU core and < 80 MB RSS attributable to the render layer.
- **SC10**: Switching avatar packs at runtime completes in < 1 s with no assistant downtime.

## 5. Functional Requirements

- **FR1** Wake-word activation (openWakeWord), always-on, CPU-resident.
- **FR2** Streaming STT with partial results; Indian English support; contact-name biasing.
- **FR3** Conversational responses from a local LLM with rolling context.
- **FR4** Tool calling: `open_app`, `list_dir`, `check_disk`, `run_command` (whitelisted), extensible registry.
- **FR5** On-demand screen understanding: mss screenshot → VLM → description.
- **FR6** Streaming TTS with sentence-level chunking; playback begins on first sentence.
- **FR7** Barge-in: user speech during SPEAKING cancels TTS and playback.
- **FR8** Tool results feed back into generation in the same turn.
- **FR9** All configuration in one `config.yaml`.
- **FR10** Avatar window: frameless, transparent-background, always-on-top, draggable; position persisted; optional click-through.
- **FR11** State-reactive animation mapped from orchestrator state, within SC8's budget; missing animations fall back per the manifest's fallback chain (Appendix B).
- **FR12** Cursor eye-tracking: pupils follow the OS cursor, clamped to socket radius, lerp-smoothed, polled ≤ 30 Hz, disableable in config.
- **FR13** Idle micro-behaviors: random blink and occasional fidget animations on jittered timers.
- **FR14** Click-to-activate: left-click emits `AVATAR_ACTIVATE` (= wake word, IDLE → LISTENING). Right-click opens a minimal menu: switch avatar, toggle eye tracking, hide avatar, quit.
- **FR15** Avatar pack switching: packs live in `avatars/`; active one selected in config, context menu, or by voice via the non-destructive `set_avatar(name)` tool. Hot-reloads without restart (the one sanctioned frozen-config exception, §15/DL-8).
- **FR16** Pack extensibility: adding an avatar = dropping a conforming pack directory into `avatars/`. No code changes.

Out of scope for v1: multi-step autonomous GUI control, multi-device access, cross-session memory, Hindi output, avatar extras (lip sync beyond a generic talking loop, physics, edge-walking, multiple avatars). See §28.

## 6. Non-functional Requirements

- **NFR1 Privacy**: no network calls except `localhost:11434` (Ollama). Avatar layer makes zero network calls; cursor coordinates never leave the render module and are never logged.
- **NFR2 Latency**: budgets per §22.
- **NFR3 Resource ceiling**: assistant idle < 5% CPU *including* avatar (avatar ≤ 3% of one core). Render loop event/timer-driven, never busy; animation timers pause on static frames.
- **NFR4 Robustness**: any stage crash contained. Avatar crash never takes down audio or orchestrator; supervisor restarts the widget; 3 crashes in 60 s disables it for the session with a spoken notice.
- **NFR5 Auditability**: `set_avatar` executions appear in the app log (not the audit log — it is not a command subprocess).

## 7. System Architecture

Single Python process (3.12 — DL-11). Concurrency domains:

1. **Audio-in OS thread** — PortAudio callback → thread-safe queue (janus). 16 kHz mono int16, blocksize 512.
2. **Unified GUI + asyncio event loop** — Qt (PySide6) integrated via **qasync**: one loop runs wake+VAD task, STT task, orchestrator, chunker, TTS worker, tool executor, interrupt bus, and avatar paint/timer events. Escape hatch: if latency CI shows loop starvation, the avatar moves behind §27 trigger (d).
3. **Audio-out OS thread** — PortAudio callback pulls PCM from a thread-safe queue.

Pipeline: mic → ring buffer → wake+VAD → STT → orchestrator → {tool executor ‖ chunker → TTS → playback}. The bus's `STATE_CHANGED` events are additionally consumed by the avatar animator; the avatar publishes `AVATAR_ACTIVATE` into the same bus.

State machine (owned solely by the orchestrator):
`IDLE →(wake | AVATAR_ACTIVATE)→ LISTENING →(VAD end)→ THINKING →(first token)→ SPEAKING →(complete|cancel)→ IDLE`; barge-in: `SPEAKING → LISTENING`. A transient `ERROR` state is published on stage failure (spoken + rendered by the avatar's error animation) before returning to `IDLE`. The avatar renders these states; it never holds its own copy of state beyond the last event received.

Barge-in ordering note (v1.2): on voice barge-in the wake stage publishes `Cancel` *and* synchronously starts capture via the orchestrator's `on_wake` hook; `on_wake` therefore cancels any in-flight turn itself — Cancel handling must never be guarded on SPEAKING state alone (race: the state has already moved to LISTENING).

## 8. Hardware Tiering

Tier selects models only — never feature behavior. The avatar is tier-independent (2D sprite blitting is trivially within budget on Tier A).

⚠ DECISION REQUIRED (D-1): **RESOLVED 2026-07-06 — Tier A (CPU-only).** Owner's machine: i7-1360P, Iris Xe, 16 GB RAM. Model table *(reconstructed)* lives in `models.md`; summary: openWakeWord + Silero VAD, faster-whisper `small` int8, Ollama `qwen2.5:3b-instruct`, Ollama `moondream`, Piper TTS.

## 9. Component Responsibilities & Module Boundaries

A module may import from `bus.py`, `config`, and its own package; cross-package communication goes through §10/§11 queue/event interfaces only.

```
jarvis/
├── main.py                 # composition root: qasync loop, threads+tasks, supervisor
├── config.yaml             # (repo root) single source of runtime config (§15)
├── bus.py                  # interrupt bus, shared events, state enum
├── logs.py                 # app log + append-only audit log (§17)
├── audio/
│   ├── input_thread.py
│   └── output_thread.py
├── perception/
│   ├── wake_vad.py
│   ├── stt.py
│   └── vision.py
├── brain/
│   ├── orchestrator.py
│   └── chunker.py
├── voice/
│   └── tts.py
├── actions/
│   ├── registry.py         # + set_avatar tool entry
│   └── executor.py
├── avatar/                 # the entire render layer
│   ├── window.py           # frameless QWidget: transparency, drag, click, tray/context menu
│   ├── animator.py         # STATE_CHANGED → animation selection; blink/fidget timers; frame stepping
│   ├── eyes.py             # cursor polling (≤30 Hz), socket clamping, lerp smoothing
│   └── packs.py            # pack discovery, manifest parsing+validation, sprite loading, hot-swap
avatars/                    # data dir — one subdirectory per pack (Appendix B)
tests/                      # mirrors package structure (§23)
```

Exclusive responsibilities: only `orchestrator.py` mutates state; only `executor.py` spawns command subprocesses; only `wake_vad.py` publishes barge-in; **only `avatar/window.py` publishes `AVATAR_ACTIVATE`; only `avatar/packs.py` reads pack files from disk.** `avatar/` imports Qt; no other package may import Qt (keeps the pipeline headless-testable).

## 10. Data Flow & Event Flow

Queue dataclasses (all `@dataclass(frozen=True, slots=True)`, defined in `bus.py`): `AudioFrame`, `PartialTranscript`, `FinalTranscript`, `Token`, `Phrase`, `PcmChunk`, `ToolCall`/`ToolResult`, `ScreenQuery`/`ScreenDescription`, plus:

- `AvatarCommand(action: Literal["set_pack","toggle_eyes","hide","show"], arg: str|None)` — orchestrator/tool-layer → avatar.
- `AvatarStatus(pack: str, visible: bool, error: str|None)` — avatar → orchestrator (informational; lets the assistant *speak* pack-load failures).

Events (bus.py): `CANCEL`; `STATE_CHANGED(new_state)` — consumed by the avatar animator, delivery latency budgeted (SC8); `AVATAR_ACTIVATE` — click-to-listen intent, consumed only by the orchestrator, **valid only in IDLE** (clicking a speaking avatar is not barge-in; barge-in stays voice-only, DL-10).

Backpressure: audio unchanged. Avatar event consumption is fire-and-forget with a **depth-1 "latest wins" mailbox** — if the animator is mid-frame when a new state arrives, it drops the stale target and jumps to the newest (animation must never queue up behind reality).

## 11. Interfaces & Contracts

External contract: **Ollama only** (`localhost:11434`). The avatar adds no external interface — Qt is a library dependency, not a service.

Internal: the §10 payloads ARE the interfaces; changing a field updates this handbook and all producers/consumers in the same commit.

**Avatar pack manifest** (`avatars/<name>/avatar.yaml`, spec Appendix B): `packs.py` validates against a schema at load; an invalid pack is rejected with a spoken+logged error and the previous pack stays active.

## 12. State & Memory Architecture

Rolling in-memory conversation context (last N turns, config `llm.context_turns`); no cross-session memory (v1). The only persisted avatar state is `{active_pack, window_x, window_y, eyes_enabled, visible}` written to `avatar_state.json` on clean shutdown (separate from config.yaml: config is frozen-at-startup and human-authored; window position is machine-written ephemera).

⚠ DECISION REQUIRED (D-2): transcripts to disk? **RESOLVED 2026-07-06 — No** (owner default accepted).

## 13. Agent, Prompt & Tool Architecture

Tools *(reconstructed)*: `open_app(name)`, `list_dir(path)`, `check_disk()`, `run_command(cmd)` — all whitelist-validated in the registry, executed only by `executor.py`, results appended to the audit log (P7) and fed back into the same generation turn (FR8). Destructive commands (`destructive: true` in the registry entry) require spoken confirmation against the resolved command string (P3). Plus:

- `set_avatar(name: str)` — `destructive: false`, no confirmation; handler publishes `AvatarCommand("set_pack", name)`; validates `name` against the discovered pack list before publishing (unknown pack → spoken "I don't have an avatar called X; I have: …"). Timeout 2 s. A tool (not intent-parsing) so *all* actions stay in the one reviewable registry, even cosmetic ones.

System prompt: assistant persona ("Diane"), tool-use format, contact-name glossary, and the names of installed avatar packs (injected from pack discovery at startup) so "switch to the cat" resolves naturally.

## 13A. Avatar Subsystem Design

**Rendering model.** Layered 2D sprite compositing per frame, back-to-front: 1) `body` layer — current animation frame; 2) `eyes` layer (optional) — socket-anchored pupil per eye, offset by the clamped, lerped cursor vector; 3) `overlay` layer (optional) — blink frames, emotes, on their own timers. (Layered instead of pre-baked eye positions: two small pupil blits per tick makes eye-tracking a *pack-manifest feature*, not a per-pack art burden.)

**Animation state mapping.** `animator.py` holds a pure function `state → animation_name` using the manifest's `states:` table with a fallback chain (`speaking → talking → idle`). Blink/fidget run on jittered timers (blink 3–8 s, fidget 20–60 s) only in `idle`; any state change cancels them (P5).

**Frame stepping.** One `QTimer` per active animation at manifest fps (cap 30). Static single-frame animations stop the timer (NFR3). Frames decoded once at pack load and cached; cache dropped on pack switch (SC6).

**Eye tracking.** `QCursor.pos()` polled by a 30 Hz timer (no global input hooks — polling is privacy-inert and portable); vector from socket center to cursor, clamped to `socket_radius`, then `pupil_pos += (target - pupil_pos) * 0.25` per tick. Wayland: degrades to window-relative tracking, logged once at startup. *(This machine runs X11 — full tracking.)*

**Window behavior.** Frameless, `WA_TranslucentBackground`, always-on-top, drag-to-move (position saved), left-click = `AVATAR_ACTIVATE`, right-click = context menu. Optional click-through mode (activation becomes wake-word-only).

**Failure containment.** Every Qt slot in `avatar/` wraps its body in try/except → log + `AvatarStatus(error=…)`; NFR4 3-strikes rule. The pipeline must run identically with `avatar.enabled: false` — CI runs the full suite headless with avatar off; avatar tests run separately offscreen.

## 14. Security

Layered model: whitelist-by-construction registry (P1/P2), confirmation on destructive resolved commands (P3), non-privileged subprocess execution with timeouts (P4), append-only audit (P7). Avatar-specific: `AVATAR_ACTIVATE` is equivalent in power to the wake word — threat delta ≈ 0. Pack loading treats every manifest field as data: numeric fields range-clamped, paths resolved and **must remain inside the pack directory** (reject `../`), images ≤ 2048×2048, ≤ 5 MB each, pack ≤ 40 MB, decoded via Qt's image loader only. No field is ever formatted into a shell command, external path, or prompt. `set_avatar` arg validated against a closed set. The avatar never touches transcripts or screen content (P9).

## 15. Configuration

Single frozen `config.yaml` (repo root; see file for the avatar section). **Frozen-config exception, justified (DL-8):** runtime pack switching doesn't carry the hazards the freeze rule targets (model paths, whitelists); pack identity is validated against a closed on-disk set, cosmetic, non-executable. `active_pack` in config is only the startup default; the runtime value lives in `avatar_state.json`.

## 16. Error Handling

Taxonomy *(reconstructed)*: `TransientError` (retry with backoff: Ollama busy, audio underrun), `PermanentError` (skip + speak: missing model, invalid input), `FatalError` (supervisor restarts stage). Avatar mappings: bad manifest/missing files → `PermanentError` for that pack (spoken notice, previous pack retained); Qt paint exceptions → contained per §13A with 3-strikes disable; pack directory disappearing mid-session → fall back to built-in code-drawn **"dot" avatar** (a pulse circle needing no assets — state visibility survives any pack failure).

## 17. Logging

Two streams: **app log** (stage lifecycle, errors, avatar events with current `turn_id`) and **audit log** (append-only, every executed command: timestamp, tool, resolved argv, exit code). Explicit prohibition: cursor coordinates, window position, and transcripts are never logged at any level (NFR1, D-2).

## 18. Coding Standards

Python 3.12, full type hints, asyncio-first; frozen dataclasses for all shared payloads; every long await cancellable (P5); Qt imports confined to `avatar/` (§9); Qt slots follow the try/except containment pattern (§13A); animator's state→animation mapping is a pure function (unit-testable without Qt); no `QApplication.processEvents()` anywhere (re-enters the loop, breaks qasync's guarantees).

## 19. Testing Strategy

- **Unit (headless, no Qt):** bus contracts; config freezing; whitelist enforcement matrix; state-machine transitions; chunker; manifest schema validation matrix (valid, traversal path, oversize image, missing state, unknown fields); animator mapping incl. fallback chain; eye-vector clamping and lerp math with synthetic cursor positions.
- **Integration:** pytest-qt offscreen (`QT_QPA_PLATFORM=offscreen`): pack load → state event → correct frame selected; hot-swap under a running frame timer; 3-strikes disable path; `AVATAR_ACTIVATE` ignored outside IDLE.
- **Latency CI:** scripted turn asserts SC8 (event→frame-selection < 100 ms) and full pipeline budgets *with the avatar running* (catches qasync loop starvation early).
- **Manual smoke:** mic wake test, barge-in, drag persistence across restart, right-click menu, click-through toggle.

## 20. Development Workflow & Milestones

Each milestone ends: tests green → handbook-auditor review → commit + push. *(M1–M7 reconstructed.)*

- **M0 — Foundation:** repo, scaffold, bus, config, logging, CI, this handbook, deps installed.
- **M1 — Ears:** audio input thread; wake word (interim `hey_jarvis`) + VAD. Exit: SC1.
- **M2 — Transcription:** streaming faster-whisper, partials, name biasing. Exit: live transcription; SC5 fixture set.
- **M3 — Brain:** orchestrator state machine + rolling context + streamed Ollama; chunker. Headless. Exit: text-in→streamed-out with correct bus events.
- **M4 — Hands:** registry + executor + audit log + FR8. Exit: SC4 by construction.
- **M5 — Voice:** Piper streaming, output thread, barge-in. Exit: SC2 (CPU), SC3.
- **M6 — Eyes:** screenshot → moondream description. Exit: SC7 (CPU).
- **M7 — Always-on:** custom "Hi Diane" wake model (trained, validated, config swap); supervisor (NFR4); systemd --user unit (`After=graphical-session.target`); SC1–SC7 measured. Handbook as-built update happens **after M8**.
- **M8 — Face:** avatar package + reference packs; window, animator, eye tracking, click-to-activate, context menu, `set_avatar`, hot-swap, fallback dot avatar; SC8–SC10 measured; latency CI extended; Appendix B validated by building the second pack purely from its instructions (the doc test: if it needs a code change, Appendix B or `packs.py` is wrong). **Must not start before M7 passes.**

## 21. Deployment

PySide6 + qasync in `pyproject.toml` (avatar extra); Qt runtime packages documented in `setup.md`. The systemd --user unit needs `After=graphical-session.target` once the avatar exists.

## 22. Latency & Resource Budgets (binding targets)

| Span | Tier A (CPU) | Tier B (GPU) | Mechanism |
|---|---|---|---|
| Wake detection | <150 ms | <100 ms | frame-level ONNX |
| End-of-utterance | ~700 ms silence | same | Silero trailing window |
| Speech-end → first token | <1500 ms | <600 ms | warm Ollama, streamed |
| First token → first audio | <800 ms | <400 ms | sentence-1 chunk → Piper |
| Barge-in stop | <100 ms | <100 ms | CANCEL + queue flush |
| State event → avatar frame | <100 ms | <100 ms | bus fan-out + latest-wins mailbox |
| Pack hot-swap | <1 s | <1 s | pre-validated manifest, lazy frame decode |
| Avatar idle CPU | <3% of one core | same | timer-driven blits, static-frame timer stop |

## 23. Known Tradeoffs (accepted deliberately)

*(T1–T6 reconstructed:)* **T1** Small local models < cloud intelligence — privacy outranks. **T2** Whitelist rigidity — safety outranks convenience. **T3** Single process couples stages — §27 triggers exist. **T4** Whisper-small on CPU limits SC5 headroom — name biasing compensates. **T5** No cross-session memory in v1. **T6** English-only output in v1.
**T7** qasync couples GUI and pipeline into one loop; pathological Qt paint could add jitter — accepted (micro-blits, measured by latency CI, §27(d) evicts it if needed). **T8** Sprite-sheet 2D only, no rigged animation — keeps packs pure data (P10). **T9** 30 Hz cursor polling — eyes may "step" under fast motion; lerp hides most. **T10** Wayland eye-tracking degradation — platform limitation (moot here: X11).

## 24. Decision Log

*(DL-1–DL-6 reconstructed from v1.1's references:)*
- **DL-1** | v1.0 | Single process, in-memory queues | *rejected:* microservices/ZeroMQ (IPC + serialization cost for a laptop app).
- **DL-2** | v1.0 | openWakeWord for wake | *rejected:* Porcupine (licensing), always-on Whisper (CPU).
- **DL-3** | v1.0 | faster-whisper streaming STT | *rejected:* vosk (accuracy), cloud STT (privacy).
- **DL-4** | v1.0 | Ollama as the only external contract | *rejected:* llama.cpp bindings in-process (crash containment, model mgmt).
- **DL-5** | v1.0 | All actions through one reviewable tool registry; voice-only barge-in interrupt analysis | *rejected:* free-form command parsing.
- **DL-6** | v1.0 | Piper TTS, sentence-chunk streaming | *rejected:* Coqui (heavier), espeak (quality).
- **DL-7** | v1.1 | Avatar rendered with PySide6 in-process via qasync — single loop, zero IPC | *rejected:* Electron/web overlay, pygame window, separate Python process.
- **DL-8** | v1.1 | Runtime avatar-pack hot-swap as the sole frozen-config exception | *rejected:* restart-to-switch, general config hot-reload.
- **DL-9** | v1.1 | Avatar packs = declarative YAML + images, no code | *rejected:* Python plugin packs.
- **DL-10** | v1.1 | Click-to-activate ≡ wake word; click never barge-ins | *rejected:* click = universal interrupt.
- **DL-11** | v1.2 | Python 3.12 (system) instead of handbook's 3.11 | all deps support 3.12; no pin-down benefit | *rejected:* installing a parallel 3.11.
- **DL-12** | v1.2 | Wake word = custom "Hi Diane"; interim `hey_jarvis` until the trained model passes a recorded validation set | owner decision 2026-07-06.
- **DL-13** | v1.2 | v1.0 sections reconstructed in this document; repo copy authoritative | owner decision 2026-07-06.

## 25. Open Questions

- **D-1**: deployment tier — **RESOLVED: Tier A** (§8).
- **D-2**: transcripts to disk — **RESOLVED: No** (§12).
- **D-3**: Wayland eye-tracking — **moot: owner session is X11**.
- **D-4** *(reconstructed)*: contact-name glossary contents — owner to supply names for `stt.contact_names` before M2 exit.
- **D-5** *(reconstructed)*: barge-in sensitivity tuning — settle during M5 with real mic testing.
- **D-6**: avatar art source — **RESOLVED 2026-07-06:** free high-quality assets chosen by the owner from surfaced platforms (itch.io, OpenGameArt, Kenney.nl); dot fallback + placeholder pack built first so M8 never blocks on art.
- **D-7**: mic-amplitude listening feedback — **deferred to §28** (default accepted).
- **D-8**: multi-monitor — **single remembered position, clamped into visible geometry at startup** (default accepted).

## 26. Repository Structure

As §9, plus root-level `avatars/` (each pack with its own `LICENSE`/`CREDITS`), `avatar_state.json` (gitignored), `setup.md`, `models.md`, `.github/workflows/ci.yaml` (headless job + `QT_QPA_PLATFORM=offscreen` avatar job), `.claude/agents/` (project subagents: handbook-auditor, milestone-verifier, module-builder).

## 27. Service-Split Triggers

Split into services only if: (a) sustained loop starvation measured, (b) a component needs a different lifecycle, (c) memory isolation required, **(d)** latency CI attributes budget violations to Qt paint jitter → avatar moves to its own process behind a one-way event socket (its P9 interface makes this a mechanical extraction).

## 28. Future Extensions (ordered by owner interest)

1. GUI control (act on screen). 2. Cloud fallback brain. 3. Cross-session memory. 4. Hindi / code-switched responses. 5. Custom cloned voice. 6. **Avatar life+**: mic-amplitude ear/gill twitch (D-7), speech-viseme mouth flaps, dragging physics, edge-walking, tool-result reactions, seasonal outfits. 7. **Community pack format v2**: rigged animation — requires revisiting T8/P10 with a sandboxed runtime; do not attempt casually.

## 29. Handbook Maintenance Rules

Any interface addition (§10), new principle (§3), or milestone change bumps the version and updates this file in the same commit as the code. ⚠ DECISION REQUIRED blocks are resolved only by the owner; record resolutions in §25 + Decision Log.

---

## Appendix A — Claude Code Kickoff Prompts

A.1/A.2 logic: pick up the first incomplete milestone from §20 and execute it to its exit criteria. Milestones run M1–M8; the avatar subsystem (M8, §13A, Appendix B) is last and **must not be started before M7 passes**.

## Appendix B — Avatar Pack Authoring Guide

Complete recipe for adding a new avatar. **If a step here requires touching Python, the pack format is broken — file it as a bug, not a workaround** (M8's doc test).

### B.1 Pack anatomy

```
avatars/
└── axolotl/                # pack name = directory name (lowercase, [a-z0-9_-])
    ├── avatar.yaml         # manifest (required)
    ├── LICENSE             # art license/credits (required)
    ├── idle/               # one directory per animation, numbered frames
    │   ├── 000.png
    │   └── 001.png
    ├── listening/
    ├── thinking/
    ├── speaking/
    ├── error/              # optional
    ├── blink/              # optional overlay
    ├── fidget_01/          # optional, any number of fidgets
    └── eyes/
        └── pupil.png       # optional; enables eye tracking
```

All frames within one pack share identical pixel dimensions. PNG with alpha. Per-image ≤ 2048×2048 and ≤ 5 MB; whole pack ≤ 40 MB (enforced by `packs.py`).

### B.2 Manifest (`avatar.yaml`)

```yaml
name: "Axolotl"             # display name for menus/voice
version: 1
author: "owner"
frame_size: [128, 128]      # must match every frame
anchor: bottom-center       # how the window hugs its position

states:                     # orchestrator state → animation directory
  idle:      {anim: idle,      fps: 8,  loop: true}
  listening: {anim: listening, fps: 12, loop: true}
  thinking:  {anim: thinking,  fps: 10, loop: true}
  speaking:  {anim: speaking,  fps: 12, loop: true}
  error:     {anim: error,     fps: 6,  loop: false}   # holds last frame

fallbacks:                  # used when a states entry or directory is missing
  speaking: idle
  thinking: idle
  listening: idle
  error: idle

overlays:
  blink:   {anim: blink, fps: 16, only_in: [idle]}
  fidgets: [fidget_01]      # picked randomly on the fidget timer

eyes:                       # omit this whole block for no eye tracking
  pupil: eyes/pupil.png     # path inside the pack only
  sockets:                  # one entry per eye, coords in frame pixels
    - {center: [46, 52], radius: 6}
    - {center: [82, 52], radius: 6}
  hide_in: [blink, error]   # states/overlays where pupils are not drawn
```

Validation rules (enforced, not advisory): unknown top-level keys rejected; every path must resolve inside the pack directory; `fps` clamped to 1–30; every `states.*.anim` and `fallbacks.*` target must exist as a frame directory; socket centers must lie within `frame_size`.

### B.3 Step-by-step: adding a new avatar

1. Copy an existing pack directory to `avatars/<yourname>/`.
2. Replace the frame PNGs. Minimum viable pack: an `idle/` directory with one frame and a manifest whose `fallbacks` route every state to `idle`.
3. Edit `avatar.yaml`: set `name`, `frame_size`, eye socket coordinates (omit `eyes:` if the character shouldn't track the cursor).
4. Add the art's `LICENSE`/credits file. Non-negotiable even for self-made art.
5. Restart the assistant *or* say "rescan avatars" / use the tray menu's rescan.
6. Say "switch to <name>" or pick it from the right-click menu. Invalid manifest → the assistant speaks the first validation error and keeps the current pack.

### B.4 Design tips for packs that feel alive

- Idle should breathe: a 2–4 frame subtle loop at low fps beats a static image.
- Listening should be instantly distinct from idle (ears up / gills flared) — this animation *is* the SC8 feedback the user relies on.
- Thinking benefits from an obvious loop (tail flick, floating bubbles).
- Keep pupils small relative to socket radius; large pupils with small travel look dead.
- Error should be endearing, not alarming — this is a companion, and errors are spoken anyway.

*End of Handbook v1.2. This repo copy is authoritative.*
