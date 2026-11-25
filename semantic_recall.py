"""Optional FastEmbed search over a project's derived node vectors."""

from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from io import StringIO
import json
import math
import os
from pathlib import Path

from egraph import write_json

MODEL = "BAAI/bge-small-en-v1.5"
MIN_SIMILARITY = 0.72
INDEX_PATH = Path(".semantic/index.json")


def backend_id() -> str:
    return f"1:{MODEL}:fastembed-{version('fastembed')}"


def load_model(root: Path, *, download: bool = False):
    # ONNX Runtime reads the telemetry opt-out during import.
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from fastembed import TextEmbedding
    from onnxruntime import disable_telemetry_events

    cache = root / ".semantic" / "models"
    if cache.is_symlink():
        raise ValueError("model cache must be local to the memory directory")
    disable_telemetry_events()
    return TextEmbedding(model_name=MODEL, cache_dir=str(cache), threads=1, local_files_only=not download)


def unit(vector) -> list[float]:
    values = [float(value) for value in vector]
    length = math.sqrt(sum(value * value for value in values))
    if not length or not math.isfinite(length):
        raise ValueError("invalid embedding")
    return [value / length for value in values]


def read_index(path: Path, backend: str) -> dict:
    if path.parent.is_symlink() or path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["backend"] == backend and isinstance(data["vectors"], dict):
            return data["vectors"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {}


def build(root: Path, documents: dict[str, str], *, download: bool = False) -> int:
    path = root / INDEX_PATH
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("semantic index must be local to the memory directory")
    backend = backend_id()
    cached = read_index(path, backend)
    vectors = {key: cached[key] for key in documents if key in cached}
    missing = {key: text for key, text in documents.items() if key not in vectors}
    if missing or (download and documents):
        model = load_model(root, download=download)
        embeddings = model.passage_embed(missing.values(), batch_size=32)
        vectors.update((key, unit(vector)) for key, vector in zip(missing, embeddings, strict=True))
    path.parent.mkdir(mode=0o700, exist_ok=True)
    write_json(path, {"backend": backend, "vectors": vectors})
    return len(vectors)


def best(root: Path, documents, query: str) -> str | None:
    # This optional dependency must never emit hook diagnostics or block lexical recall.
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            vectors = read_index(root / INDEX_PATH, backend_id())
            available = documents.keys() & vectors.keys()
            if not available:
                return None
            embedding = unit(next(load_model(root).query_embed(query)))
            # shortcut: scan cached vectors until project size warrants a vector index.
            scores = [(sum(a * b for a, b in zip(embedding, vectors[key], strict=True)), key)
                      for key in sorted(available) if len(embedding) == len(vectors[key])]
            score, key = max(scores, default=(0, None))
            return key if MIN_SIMILARITY <= score <= 1.000001 else None
    except Exception:
        return None
