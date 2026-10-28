import pytest

from src.normalize import normalize_date, normalize_merchant, normalize_total


@pytest.mark.parametrize("raw,expected", [
    ("25/12/2018", "2018-12-25"),
    ("2018-12-25", "2018-12-25"),
    ("25 Dec 2018", "2018-12-25"),
    ("25-Dec-18", "2018-12-25"),
    ("Dec 25, 2018", "2018-12-25"),
    ("25.12.2018", "2018-12-25"),
    ("25/12/18", "2018-12-25"),
    ("12/25/2018", "2018-12-25"),           # day-first impossible -> month-first
    ("01/02/2018", "2018-02-01"),           # ambiguous -> day-first (SROIE/CORD)
    ("Date: 05/01/2019 10:42 AM", "2019-01-05"),
    ("2018/1/5", "2018-01-05"),
    ("20181225", "2018-12-25"),
    ("29/02/2020", "2020-02-29"),
])
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "hello", "31/02/2018", "99/99/9999", "2018-13-01"])
def test_normalize_date_bad_input_returns_none(raw):
    assert normalize_date(raw) is None


@pytest.mark.parametrize("raw,expected", [
    # SROIE / western style: decimal point, comma thousands
    ("12.50", "12.50"),
    ("RM 9.90", "9.90"),
    ("$1,234.50", "1234.50"),
    (" 1 234.50 ", "1234.50"),
    ("12.5", "12.50"),
    ("45", "45.00"),
    (12.5, "12.50"),
    (7, "7.00"),
    ("12,50", "12.50"),                      # decimal comma
    ("1.234,56", "1234.56"),                 # European
    ("-3.20", "-3.20"),
    # CORD / rupiah style: separator + exactly 3 digits = thousands
    ("60.000", "60000.00"),
    ("Rp 60.000", "60000.00"),
    ("1,591,600", "1591600.00"),
    ("45.500", "45500.00"),
    ("Rp. 1.591.600", "1591600.00"),
    ("IDR 22,000", "22000.00"),
    # real CORD-v2 formats
    ("Rp. 91,000", "91000.00"),
    ("Rp 38.000", "38000.00"),
    ("Rp.118.000", "118000.00"),
    ("365000.00", "365000.00"),
    ("63,000.00", "63000.00"),
    ("9.500,00", "9500.00"),
])
def test_normalize_total(raw, expected):
    assert normalize_total(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "RM", True, "12.3456", "1..2"])
def test_normalize_total_bad_input_returns_none(raw):
    assert normalize_total(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("  SUNRISE   Bakery ", "sunrise bakery"),
    ("ABC Trading Sdn. Bhd.", "abc trading sdn bhd"),
    ("Café-Ümlaut", "café ümlaut"),
    ("STRASSE", "strasse"),
])
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "...", "-- !"])
def test_normalize_merchant_bad_input_returns_none(raw):
    assert normalize_merchant(raw) is None


SAMPLES = ["25/12/2018", "2018-12-25", "25 Dec 2018", "60.000", "1,591,600", "12.50",
           "RM 9.90", "12,50", "45", "ABC Trading Sdn. Bhd.", "  x  y ", "", None, "junk"]


@pytest.mark.parametrize("fn", [normalize_date, normalize_total, normalize_merchant])
@pytest.mark.parametrize("raw", SAMPLES)
def test_idempotent(fn, raw):
    once = fn(raw)
    assert fn(once) == once


@pytest.mark.parametrize("fn", [normalize_date, normalize_total, normalize_merchant])
def test_never_raises_on_odd_types(fn):
    for v in [None, 0, 3.14, [], {}, object(), b"bytes", "🙂"]:
        fn(v)  # must not raise


# All merchant names and registration numbers below are made up.
@pytest.mark.parametrize("raw,expected", [
    ("ACME HARDWARE SDN BHD (123456-A)", "acme hardware sdn bhd"),
    ("QUICKMART S/B (654321-B)", "quickmart s b"),
    ("ACME TRADING (7654321-C)", "acme trading"),
    ("ZEPHYR ENTERPRISE (000123-D)", "zephyr enterprise"),
    ("ACME HARDWARE SDN BHD ( 123456 - a ) ", "acme hardware sdn bhd"),
    ("ACME HARDWARE SDN BHD CO. REG (123456-A)", "acme hardware sdn bhd"),
    ("ACME HARDWARE SDN BHD Co. Reg. No: (123456-A)", "acme hardware sdn bhd"),
    ("ACME HARDWARE SDN BHD CO REG NO.(123456-A)", "acme hardware sdn bhd"),
    ("ACME HARDWARE SDN BHD COMPANY NO. (123456-A)", "acme hardware sdn bhd"),
    ("ACME HARDWARE SDN BHD Company No: (123456-A)", "acme hardware sdn bhd"),
])
def test_merchant_strips_trailing_registration_number(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("ZEPHYR CO. (M) BHD", "zephyr co m bhd"),              # mid-name '(M)' is kept
    ("ACME BOOK CO. (123456-A)", "acme book co"),           # plain 'CO.' is not a label
    ("ACME (123456-A) MART", "acme 123456 a mart"),         # only a trailing number
    ("ACME (1234-A)", "acme 1234 a"),                       # too short
    ("ACME (123456789-A)", "acme 123456789 a"),             # too long
    ("ACME (123456 A)", "acme 123456 a"),                   # no hyphen: not the pattern
    ("ACME (123456-AB)", "acme 123456 ab"),
    ("(123456-A)", None),
])
def test_merchant_registration_number_edge_cases(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("raw", ["QUICKMART S/B (654321-B)", "X CO. REG (12345-A)",
                                 "ZEPHYR CO. (M) BHD", "(123456-A)", "", "  "])
def test_merchant_reg_strip_idempotent(raw):
    once = normalize_merchant(raw)
    assert normalize_merchant(once) == once


def test_merchant_bad_input_never_raises():
    class Bad:
        def __str__(self):
            raise ValueError("nope")
    assert normalize_merchant(Bad()) is None
    assert normalize_merchant(None) is None
    assert normalize_merchant(12345) == "12345"
