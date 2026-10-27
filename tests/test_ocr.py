import pytest

from src import ocr
from tests.conftest import FIXTURES

pytestmark = pytest.mark.skipif(not ocr.tesseract_available(), reason="tesseract not installed")
IMG = FIXTURES / "images" / "synthetic_001.png"


def test_tesseract_reads_fixture():
    text = ocr.ocr_image(IMG, engine="tesseract").upper()
    assert "SUNRISE" in text and "11.20" in text


def test_threshold_option():
    assert "SUNRISE" in ocr.ocr_image(IMG, engine="tesseract", threshold=128).upper()


def test_cache_written_and_reused(tmp_path):
    t1, s1 = ocr.ocr_cached("synthetic_001", IMG, engine="tesseract", cache_dir=tmp_path)
    assert (tmp_path / "tesseract" / "synthetic_001.txt").exists() and s1 > 0
    t2, s2 = ocr.ocr_cached("synthetic_001", IMG, engine="tesseract", cache_dir=tmp_path)
    assert t1 == t2 and s2 == 0.0


def test_version_and_errors():
    assert ocr.engine_version("tesseract").startswith("tesseract-")
    with pytest.raises(FileNotFoundError):
        ocr.ocr_image(FIXTURES / "nope.png", engine="tesseract")
    with pytest.raises(ValueError):
        ocr.ocr_image(IMG, engine="magic")


def test_auto_resolves_to_installed_engine():
    assert ocr.resolve_engine("auto") in ocr.PREFERRED_ENGINES
    assert ocr.resolve_engine("tesseract") == "tesseract"


@pytest.mark.skipif(not ocr.engine_available("doctr"), reason="docTR not installed")
def test_doctr_reads_fixture():
    assert "SUNRISE" in ocr.ocr_image(IMG, engine="doctr").upper()
