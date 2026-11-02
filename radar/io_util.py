"""Save/load IQ captures as .npy with a JSON sidecar of metadata."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


def timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (complex, np.complexfloating)):
        return [float(o.real), float(o.imag)]
    return str(o)


def save_json(path, obj) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_jsonable))
    return path


def save_iq(iq: np.ndarray, meta: dict, out_dir=DATA_DIR / "captures",
            name: str | None = None) -> Path:
    """Write <name>.npy (complex64) and <name>.json. Returns the .npy path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name or f"capture_{timestamp()}"
    npy = out_dir / f"{name}.npy"
    np.save(npy, np.asarray(iq, dtype=np.complex64))
    meta = dict(meta)
    meta.setdefault("saved_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    meta["num_samples"] = int(np.asarray(iq).size)
    meta["dtype"] = "complex64"
    save_json(npy.with_suffix(".json"), meta)
    return npy


def load_iq(path):
    """Load (iq, meta) from a .npy or .json path."""
    path = Path(path)
    npy = path.with_suffix(".npy")
    js = path.with_suffix(".json")
    iq = np.load(npy)
    meta = json.loads(js.read_text()) if js.exists() else {}
    return iq, meta
