"""Train the "Hi Diane" wake-word classifier on openWakeWord embeddings (M7, DL-12).

Pipeline: wav → openWakeWord melspec+embedding features (the same frontend the
runtime uses) → sliding windows of 16 embedding frames → small MLP → ONNX.
The runtime scorer (jarvis/perception/wake_vad.py CustomWakeWord) feeds the
identical streaming features, so train/serve skew is minimal.

Run:  .venv/bin/python scripts/train_wakeword/train.py
Writes models/hi_diane.onnx + prints train/holdout metrics.
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "models/wakeword_data"
OUT = ROOT / "models/hi_diane.onnx"
WINDOW = 16  # embedding frames per classification window (~1.28 s of audio)


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def _embed(pcm: np.ndarray) -> np.ndarray:
    """openWakeWord embedding frames for a clip: (n_frames, 96)."""
    from openwakeword.utils import AudioFeatures

    feats = AudioFeatures(inference_framework="onnx")
    feats(pcm)
    return np.array(feats.feature_buffer)  # (n_frames, 96)


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
    return [emb[s : s + WINDOW].ravel() for s in range(0, len(emb) - WINDOW + 1, 4)]


def main() -> None:
    xs, ys = [], []
    for kind, label in (("positive", 1), ("negative", 0)):
        files = sorted((DATA / kind).glob("*.wav"))
        print(f"embedding {len(files)} {kind} clips...")
        for i, f in enumerate(files):
            emb = _embed(_read_wav(f))
            for w in _windows(emb, positive=label == 1):
                xs.append(w)
                ys.append(label)
    x = np.array(xs, dtype=np.float32)
    y = np.array(ys)
    print(f"dataset: {x.shape}, positives={int(y.sum())}, negatives={int((1 - y).sum())}")

    from sklearn.model_selection import train_test_split
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    xtr, xte, ytr, yte = train_test_split(x, y, test_size=0.2, random_state=0, stratify=y)
    clf = make_pipeline(
        StandardScaler(),
        MLPClassifier(hidden_layer_sizes=(128,), max_iter=400, early_stopping=True,
                      random_state=0),
    )
    clf.fit(xtr, ytr)

    from sklearn.metrics import classification_report

    proba = clf.predict_proba(xte)[:, 1]
    print(classification_report(yte, proba > 0.5, target_names=["neg", "pos"]))
    # false-accept rate at the runtime threshold matters most for always-on
    for thr in (0.5, 0.7, 0.9):
        fa = float(np.mean(proba[yte == 0] > thr))
        fr = float(np.mean(proba[yte == 1] <= thr))
        print(f"  thr={thr}: false-accept={fa:.4f} false-reject={fr:.4f}")

    from skl2onnx import to_onnx

    onnx_model = to_onnx(clf, x[:1], options={"zipmap": False})
    OUT.write_bytes(onnx_model.SerializeToString())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
