"""Sentence vectors for the answer bank, computed on this machine.

Why this exists at all. The bank's own matching is lexical, and lexical
matching has a ceiling that two real pairs sit directly on top of:

    asked   "Why are you interested in this role?"
    stored  "Why do you want to work here?"

    asked   "Describe a challenging project you worked on"
    stored  "Tell us about a time you solved a hard technical problem"

Both pairs are the same question. Neither shares a single content word, so
every string method scores them zero — see the note on
``learning.relevance``, which is measured and says so. They are also exactly
the questions worth retrieving for, because they are the long free-text ones
a model is about to compose an answer to.

Why *local*. The lookup path's whole claim is that it costs nothing: no
agent, no model call, and it works with no model configured at all. Recall
runs per field on every form. An embedding API call in that loop would
quietly undo all three. So the model runs here, on the CPU, offline.

Why ONNX rather than torch. all-MiniLM-L6-v2 is 384 dimensions and about
90MB as a quantised ONNX file. The same model through torch drags in 2.5GB
of CUDA wheels for a desktop app that otherwise fits in a few hundred. The
runtime is ``onnxruntime`` and the tokenizer is ``tokenizers``; both are
small and neither needs a GPU.

Everything here is optional. No model on disk, no runtime installed, a
download that fails — all of it degrades to ``None`` and the bank carries on
with the lexical matching it has always had. An answer bank that is slightly
deaf is a feature working less well; one that raises on import is an app
that will not start.
"""
from __future__ import annotations

import hashlib
import logging
import struct
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: 384 dimensions, 6 layers, trained for sentence similarity rather than
#: token prediction — which is the distinction that matters here. Quantised
#: to int8: on this task the recall difference against the full-precision
#: model is not measurable, and it is a third of the size.
MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
BASE_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/main/"
DIMS = 384


def _model_file() -> str:
    """Which build of the weights this machine wants.

    The repo ships the quantised weights per instruction set rather than one
    portable file — 23MB each against 90MB for the full-precision one, which
    on a download somebody is waiting through is worth choosing properly.
    Anything that is not obviously x86-64 or ARM gets the portable file
    rather than a guess: a model that will not load is worse than a slower
    download, and it fails at load time where there is nothing to say about
    it.
    """
    import platform

    machine = (platform.machine() or "").lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "onnx/model_quint8_avx2.onnx"
    if machine in {"arm64", "aarch64"}:
        return "onnx/model_qint8_arm64.onnx"
    return "onnx/model.onnx"


def model_files() -> dict[str, str]:
    return {"model": _model_file(), "tokenizer": "tokenizer.json"}

#: How long a question may be before it is cut. These are form labels, not
#: documents; the model's own limit is 256 tokens and nothing here comes
#: close.
MAX_CHARS = 1200

_lock = threading.Lock()
#: Loaded once per process and kept. ``False`` means we tried and could not,
#: which must not be retried on every call — a missing runtime does not
#: become present between two lookups on the same form.
_session: Any = None
_tokenizer: Any = None
_tried = False


def model_dir() -> Path:
    from .config import DATA_DIR

    return DATA_DIR / "embed"


def installed() -> bool:
    """Whether the model is on disk. No network, no import, no load."""
    d = model_dir()
    return all((d / Path(name).name).exists()
               for name in model_files().values())


def runtime_available() -> bool:
    """Whether the two libraries this needs can be imported."""
    import importlib.util

    return all(importlib.util.find_spec(m) is not None
               for m in ("onnxruntime", "tokenizers"))


def available() -> bool:
    """Whether :func:`encode` can be expected to return vectors."""
    return runtime_available() and installed()


def describe() -> str:
    """One line for the Settings page, saying which of the two it is."""
    if not runtime_available():
        return ("Not installed. Sushi matches questions by their wording "
                "only, which misses ones asked in different words.")
    if not installed():
        mb = 90 if _model_file().endswith("model.onnx") else 24
        return (f"Ready to download (about {mb}MB, once). Until then Sushi "
                f"matches questions by their wording only.")
    return f"On, running on this machine ({MODEL_REPO}, {DIMS} dimensions)."


def fetch(on_progress: Any = None) -> bool:
    """Download the model. Returns whether it is usable afterwards.

    Written to a temporary name and moved into place, so a download killed
    halfway leaves nothing behind that :func:`installed` would call a model
    and :func:`_load` would then fail on for the rest of the install's life.
    """
    import shutil
    import urllib.request

    if installed():
        return True
    if not runtime_available():
        log.info("embed: onnxruntime/tokenizers are not installed")
        return False
    d = model_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.info("embed: cannot make %s (%s)", d, exc)
        return False
    for label, name in model_files().items():
        out = d / Path(name).name
        if out.exists():
            continue
        tmp = out.with_suffix(out.suffix + ".part")
        if on_progress:
            on_progress(f"Downloading the matcher ({label})…")
        try:
            with urllib.request.urlopen(BASE_URL + name, timeout=120) as resp, \
                    open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            tmp.replace(out)
        except Exception as exc:              # noqa: BLE001
            log.info("embed: could not download %s (%s)", name, exc)
            tmp.unlink(missing_ok=True)
            return False
    # Loaded once here rather than left for the first real lookup, so a
    # broken download is reported now, while somebody is looking at it.
    global _tried
    _tried = False
    ok = _load()
    log.info("embed: downloaded and %s", "ready" if ok else "FAILED to load")
    return ok


def _load() -> bool:
    """Bring up the runtime and the tokenizer. Once, and never twice."""
    global _session, _tokenizer, _tried
    with _lock:
        if _tried:
            return _session is not None
        _tried = True
        if not available():
            log.info("embed: no local model (runtime=%s, files=%s)",
                     runtime_available(), installed())
            return False
        try:
            import onnxruntime
            from tokenizers import Tokenizer

            d = model_dir()
            # One thread: this is called from the UI's worker threads while a
            # browser is driving, and a model that grabs every core to embed
            # a six-word form label is a worse neighbour than a slow one.
            opts = onnxruntime.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            _session = onnxruntime.InferenceSession(
                str(d / Path(model_files()["model"]).name),
                sess_options=opts, providers=["CPUExecutionProvider"])
            _tokenizer = Tokenizer.from_file(
                str(d / Path(model_files()["tokenizer"]).name))
            _tokenizer.enable_truncation(256)
            _tokenizer.enable_padding()
            log.info("embed: local model ready (%s)", MODEL_REPO)
            return True
        except Exception as exc:              # noqa: BLE001 — see the docstring
            log.info("embed: could not load the local model (%s)", exc)
            _session = _tokenizer = None
            return False


def encode(texts: list[str]) -> list[list[float]] | None:
    """Vectors for ``texts``, or None if there is no model to do it with.

    Mean-pooled over the attention mask and L2-normalised, which is what
    sentence-transformers does for this model and what its training assumed.
    Skipping either step does not fail loudly — it just returns vectors whose
    similarities are wrong, which is far worse.
    """
    if not texts or not _load():
        return None
    try:
        import numpy as np

        enc = _tokenizer.encode_batch([t[:MAX_CHARS] for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        # Some exports of this model want token_type_ids and some do not.
        names = {i.name for i in _session.get_inputs()}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        feed = {k: v for k, v in feed.items() if k in names}
        out = _session.run(None, feed)[0]            # (batch, tokens, dims)

        m = mask[..., None].astype(np.float32)
        pooled = (out * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.clip(norm, 1e-9, None)
        return pooled.astype(np.float32).tolist()
    except Exception as exc:                  # noqa: BLE001
        log.info("embed: could not encode (%s)", exc)
        return None


def similarity(a: list[float], b: list[float]) -> float:
    """Cosine, for vectors that are already unit length — so, a dot product."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


# --- keeping them ------------------------------------------------------------
#
# A vector is expensive to compute once and free to store, and the bank's
# questions do not change. They are cached by a hash of the text rather than
# by the bank's key, so an answer that is re-worded gets a new vector and a
# forgotten one leaves a row nothing will ever ask for again — which is
# cheaper than keeping the two tables in step.


def text_id(text: str) -> str:
    return hashlib.sha1(text[:MAX_CHARS].encode("utf-8")).hexdigest()[:16]


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(raw: bytes) -> list[float]:
    if not raw or len(raw) % 4:
        return []
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))
