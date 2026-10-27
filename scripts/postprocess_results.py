"""Write a post-processed copy of a results file (merchant only, pp-merchant-v1).

The input file is never modified. Each receipt in the output keeps the raw prediction
in `merchant_raw` and lists the rules that fired in `merchant_pp_rules`; the run gets
`postprocess: "pp-merchant-v1"` and `extractor_version: "<raw>+pp-merchant-v1"`.
Score the output with src/evaluate.py or scripts/paired_eval.py as a separate
'post-processed' score next to the raw one.

  python scripts/postprocess_results.py results/raw.json results/raw__pp.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.postprocess import VERSION, postprocess_merchant  # noqa: E402


def postprocess_results(results: dict) -> tuple[dict, Counter]:
    out = copy.deepcopy(results)
    counts: Counter = Counter()
    for rec in out.get("receipts", []):
        raw = rec.get("merchant")
        new, fired = postprocess_merchant(raw)
        rec["merchant_raw"] = raw
        rec["merchant_pp_rules"] = fired
        rec["merchant"] = new
        counts.update(fired)
        counts["receipts_changed"] += new != raw
    out["postprocess"] = VERSION
    out["extractor_version"] = f"{results.get('extractor_version')}+{VERSION}"
    if "run_id" in out:
        out["run_id"] = f"{out['run_id']}__pp"
    return out, counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inp"); p.add_argument("out")
    a = p.parse_args(argv)
    if Path(a.inp).resolve() == Path(a.out).resolve():
        p.error("refusing to overwrite the raw results file")
    out, counts = postprocess_results(json.loads(Path(a.inp).read_text(encoding="utf-8")))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
