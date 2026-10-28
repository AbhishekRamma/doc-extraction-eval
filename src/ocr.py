"""Image -> text, with pluggable OCR engines and a file cache.

Engines (all free, local, CPU):
- "tesseract" (baseline): pytesseract + the tesseract binary
- "auto" (default): docTR if installed, else tesseract (see resolve_engine)
- "easyocr", "doctr", "paddleocr": optional; imported lazily and only
  available if their package is installed (see scripts/compare_ocr.py).

Cache: data/ocr_cache/<engine>/<id>.txt (tesseract also uses this layout).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "ocr_cache"
BASELINE_ENGINE = "tesseract"
# Default picked from measured results (docs/RESULTS.md, scripts/compare_ocr.py):
# on the 20-receipt CORD quick split, docTR + rules got 16/19 totals right at
# ~0.44 s/image vs tesseract 8/19 at ~0.29 s/image. "auto" = docTR when installed,
# otherwise the tesseract baseline.
PREFERRED_ENGINES = ["doctr", "tesseract"]
DEFAULT_ENGINE = "auto"


def preprocess(img: Image.Image, threshold: int | None = None) -> Image.Image:
    """Tiny optional clean-up: grayscale, autocontrast, optional binarise."""
    g = ImageOps.autocontrast(img.convert("L"))
    if threshold is not None:
        g = g.point(lambda p: 255 if p > threshold else 0)
    return g


# ---- engines -------------------------------------------------------------
# Each engine is a function (PIL image) -> text. Heavy models are loaded once.

_models: dict[str, object] = {}


def _tesseract(img: Image.Image) -> str:
    import pytesseract
    return pytesseract.image_to_string(img, config="--psm 6")


def _easyocr(img: Image.Image) -> str:
    import numpy as np
    if "easyocr" not in _models:
        import easyocr
        _models["easyocr"] = easyocr.Reader(["en"], gpu=False, verbose=False)
    lines = _models["easyocr"].readtext(np.array(img.convert("RGB")), detail=0, paragraph=False)
    return "\n".join(lines)


def _doctr(img: Image.Image) -> str:
    import numpy as np
    if "doctr" not in _models:
        from doctr.models import ocr_predictor
        _models["doctr"] = ocr_predictor(pretrained=True)
    doc = _models["doctr"]([np.array(img.convert("RGB"))])
    out = []
    for page in doc.pages:
        for block in page.blocks:
            for line in block.lines:
                out.append(" ".join(w.value for w in line.words))
    return "\n".join(out)


def _paddleocr(img: Image.Image) -> str:
    import numpy as np
    if "paddleocr" not in _models:
        from paddleocr import PaddleOCR
        # oneDNN (mkldnn) crashed on this CPU with paddlepaddle 3.x; plain CPU works.
        # Document orientation/unwarping models are off: receipts are upright photos.
        _models["paddleocr"] = PaddleOCR(lang="en", enable_mkldnn=False,
                                         use_doc_orientation_classify=False,
                                         use_doc_unwarping=False,
                                         use_textline_orientation=False)
    res = _models["paddleocr"].predict(np.array(img.convert("RGB")))
    out = []
    for page in res or []:
        texts = page.get("rec_texts") if hasattr(page, "get") else None
        if texts:
            out.extend(texts)
    return "\n".join(out)


ENGINES: dict[str, Callable[[Image.Image], str]] = {
    "tesseract": _tesseract,
    "easyocr": _easyocr,
    "doctr": _doctr,
    "paddleocr": _paddleocr,
}

_MODULES = {"tesseract": "pytesseract", "easyocr": "easyocr",
            "doctr": "doctr", "paddleocr": "paddleocr"}


def engine_available(name: str) -> bool:
    import importlib.util
    if name not in ENGINES:
        return False
    if importlib.util.find_spec(_MODULES[name]) is None:
        return False
    if name == "tesseract":
        return shutil.which("tesseract") is not None
    return True


def available_engines() -> list[str]:
    return [n for n in ENGINES if engine_available(n)]


def tesseract_available() -> bool:
    return engine_available("tesseract")


def engine_version(name: str) -> str:
    """Return e.g. 'tesseract-5.5.0' using the version detected at runtime."""
    try:
        name = resolve_engine(name)
        if name == "tesseract":
            import pytesseract
            return f"tesseract-{pytesseract.get_tesseract_version()}"
        from importlib.metadata import version
        pkg = {"doctr": "python-doctr"}.get(name, name)
        return f"{name}-{version(pkg)}"
    except Exception:
        return f"{name}-unknown"


def resolve_engine(name: str) -> str:
    """Map 'auto' to the best installed engine; check explicit names exist."""
    if name == "auto":
        for e in PREFERRED_ENGINES:
            if engine_available(e):
                return e
        raise RuntimeError("no OCR engine available (install tesseract-ocr)")
    if name not in ENGINES:
        raise ValueError(f"unknown OCR engine {name!r}; choose from {['auto', *ENGINES]}")
    return name


def ocr_image(path: str | Path, engine: str = DEFAULT_ENGINE,
              threshold: int | None = None, do_preprocess: bool = True) -> str:
    engine = resolve_engine(engine)
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    with Image.open(path) as img:
        img.load()
        if do_preprocess and engine == "tesseract":
            img = preprocess(img, threshold)
        return ENGINES[engine](img)


def ocr_cached(receipt_id: str, path: str | Path, engine: str = DEFAULT_ENGINE,
               cache_dir: Path | None = None, **kw) -> tuple[str, float]:
    """OCR with a text cache. Returns (text, seconds spent; 0.0 on cache hit)."""
    engine = resolve_engine(engine)
    cache = (cache_dir or CACHE_DIR) / engine / f"{receipt_id}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8"), 0.0
    t0 = time.perf_counter()
    text = ocr_image(path, engine=engine, **kw)
    dt = time.perf_counter() - t0
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text, dt


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OCR one image")
    p.add_argument("image")
    p.add_argument("--engine", default=DEFAULT_ENGINE, choices=["auto", *ENGINES])
    p.add_argument("--threshold", type=int, default=None)
    a = p.parse_args()
    print(ocr_image(a.image, engine=a.engine, threshold=a.threshold))
