"""Train the "Hi Diane" wake-word classifier on openWakeWord embeddings (M7, DL-12).

Pipeline: wav → openWakeWord melspec+embedding features → sliding windows of
16 embedding frames → small MLP → ONNX.

Features are computed by STREAMING each clip in 1280-sample chunks — exactly
how the runtime scorer (jarvis/perception/wake_vad.py CustomWakeWord) feeds
the mic. The first version embedded whole clips in one call, which produces
different features; the deployed model then fired on most *other* voices'
speech at runtime (hundreds of false wakes/day) while looking perfect on its
own holdout. Evaluation here is clip-level, through the same streaming path.

Run:  .venv/bin/python scripts/train_wakeword/train.py [out.onnx]
Writes models/hi_diane.onnx (or the given path) + prints holdout metrics.
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "models/wakeword_data"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models/hi_diane.onnx"
CHUNK = 1280  # runtime feed size (80 ms)
WINDOW = 16  # embedding frames per classification window (~1.28 s of audio)


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def _pad(pcm: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Surround a clip with low-level noise, as a live mic stream would.

    The 1.5 s lead-in matters: at runtime the 16-frame window always holds
    ambient frames before the phrase, never zero-padding or a cold buffer.
    """
    def noise(n: int) -> np.ndarray:
        return rng.normal(0, 30, n).astype(np.int16)

    return np.concatenate([noise(24000), pcm, noise(8000)])


def _embed(pcm: np.ndarray) -> np.ndarray:
    """Streamed openWakeWord embedding frames for a clip: (n_frames, 96).

    One frame per 1280-sample chunk, fed chunk by chunk like the runtime.
    """
    from openwakeword.utils import AudioFeatures

    feats = AudioFeatures(inference_framework="onnx")
    rows = []
    for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
        feats(pcm[i : i + CHUNK])
        rows.append(np.array(feats.feature_buffer[-1]))
    return np.array(rows) if rows else np.zeros((0, 96), np.float32)


def _windows(emb: np.ndarray, positive: bool) -> list[np.ndarray]:
    """Flattened WINDOW-frame slices. Positives: the peak-energy-aligned windows
    (the phrase sits somewhere in the clip); negatives: every stride-4 window."""
    if len(emb) < WINDOW:
        pad = np.zeros((WINDOW - len(emb), emb.shape[1]), dtype=emb.dtype)
        emb = np.vstack([pad, emb])
    if positive:
        # align on the phrase: pick the windows with the highest embedding energy
        norms = np.linalg.norm(emb, axis=1)
        scores = [
            (norms[s : s + WINDOW].sum(), s) for s in range(0, len(emb) - WINDOW + 1, 1)
        ]
        scores.sort(reverse=True)
        starts = sorted({s for _, s in scores[:3]})
        return [emb[s : s + WINDOW].ravel() for s in starts]
    return [emb[s : s + WINDOW].ravel() for s in range(0, len(emb) - WINDOW + 1, 2)]


REAL_OVERSAMPLE = 12  # each real-mic window counts this many times vs synthetic
REAL_HOLDOUT = 2      # real positive clips kept out of training for validation
HOLDOUT_FRAC = 0.2    # synthetic clips held out (by CLIP, not window) for evaluation


class _OnnxModel:
    """Score with an existing ONNX wake model through the same harness (baseline)."""

    def __init__(self, path: Path) -> None:
        import onnxruntime as ort

        self._sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._inp = self._sess.get_inputs()[0].name

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return self._sess.run(None, {self._inp: x})[1]


def _report(name: str, clf, held: dict) -> None:
    print(f"--- {name}: clip-level holdout (runtime streaming view)")
    for thr in (0.5, 0.6, 0.7, 0.8):
        for patience in (1, 2, 3):
            def fires(emb: np.ndarray) -> bool:
                return _max_streak(_clip_scores(clf, emb), thr) >= patience

            fa = np.mean([fires(e) for e in held["neg"]]) if held["neg"] else 0.0
            fr = 1 - np.mean([fires(e) for e in held["pos"]]) if held["pos"] else 0.0
            real = [fires(e) for e in held["real_pos"]]
            realn = [fires(e) for e in held["real_neg"]]
            print(f"  thr={thr} patience={patience}: false-accept={fa:.3f} "
                  f"false-reject={fr:.3f}  real-pos {sum(real)}/{len(real)}  "
                  f"real-neg fired {sum(realn)}/{len(realn)}")


def _clip_scores(clf, emb: np.ndarray) -> np.ndarray:
    """Per-chunk score as the runtime sees it: last WINDOW frames after each chunk."""
    if len(emb) < WINDOW:
        return np.zeros(1)
    ws = np.array([emb[i - WINDOW : i].ravel() for i in range(WINDOW, len(emb) + 1)])
    return clf.predict_proba(ws.astype(np.float32))[:, 1]


def _max_streak(scores: np.ndarray, thr: float) -> int:
    best = cur = 0
    for s in scores:
        cur = cur + 1 if s >= thr else 0
        best = max(best, cur)
    return best


def main() -> None:
    rng = np.random.default_rng(0)
    split = np.random.default_rng(1)
    xs, ys = [], []
    held: dict[str, list[np.ndarray]] = {"pos": [], "neg": [], "real_pos": [], "real_neg": []}

    for kind, label in (("positive", 1), ("negative", 0)):
        files = sorted((DATA / kind).glob("*.wav"))
        print(f"embedding {len(files)} {kind} clips...")
        for f in files:
            emb = _embed(_pad(_read_wav(f), rng))
            if split.random() < HOLDOUT_FRAC:
                held["pos" if label else "neg"].append(emb)
                continue
            for w in _windows(emb, positive=label == 1):
                xs.append(w)
                ys.append(label)

    # Real-mic samples (domain adaptation, DL-12): oversampled, minus a holdout.
    for kind, label in (("real_positive", 1), ("real_negative", 0)):
        files = sorted((DATA / kind).glob("*.wav"))
        if len(files) > REAL_HOLDOUT:
            for f in files[-REAL_HOLDOUT:]:
                held["real_pos" if label else "real_neg"].append(_embed(_pad(_read_wav(f), rng)))
            files = files[:-REAL_HOLDOUT]
        print(f"embedding {len(files)} {kind} clips (x{REAL_OVERSAMPLE})...")
        for f in files:
            emb = _embed(_pad(_read_wav(f), rng))
            for w in _windows(emb, positive=label == 1):
                for _ in range(REAL_OVERSAMPLE):
                    xs.append(w)
                    ys.append(label)

    x = np.array(xs, dtype=np.float32)
    y = np.array(ys)
    print(f"dataset: {x.shape}, positives={int(y.sum())}, negatives={int((1 - y).sum())}")

    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    clf = make_pipeline(
        StandardScaler(),
        MLPClassifier(hidden_layer_sizes=(128,), max_iter=400, early_stopping=True,
                      alpha=1e-3, random_state=0),
    )
    clf.fit(x, y)

    # Clip-level holdout through the runtime's streaming view — the honest metric.
    deployed = ROOT / "models/hi_diane.onnx"
    if deployed.exists() and deployed.resolve() != OUT.resolve():
        _report(f"baseline {deployed.name}", _OnnxModel(deployed), held)
    _report("new model", clf, held)

    from skl2onnx import to_onnx

    onnx_model = to_onnx(clf, x[:1], options={"zipmap": False})
    OUT.write_bytes(onnx_model.SerializeToString())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
