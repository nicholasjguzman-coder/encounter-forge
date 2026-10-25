"""Compile every SRD monster and spell into the simulator schema with the Rules Compiler agent.

Resumable: entries already in data/compiled/<edition>/*.json are skipped.
Usage: python tools/compile_srd.py [--only NAME ...] [--workers 16]
"""
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dmsim.compiler import compile_monster, compile_spell  # noqa: E402

lock = threading.Lock()


def run(edition, kind, only, workers):
    raw = json.loads((ROOT / "data/raw" / edition / f"{kind}.json").read_text())
    out_path = ROOT / "data/compiled" / edition / f"{kind}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    todo = [r for r in raw if r["index"] not in done and (not only or r["name"] in only)]
    fn = compile_monster if kind == "monsters" else compile_spell
    failed = []

    def job(r):
        return r["index"], fn(r, edition).model_dump(exclude_none=True)

    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(job, r): r for r in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                idx, data = f.result()
                with lock:
                    done[idx] = data
                    if i % 20 == 0:
                        out_path.write_text(json.dumps(done, indent=1))
            except Exception as e:  # keep going; failures are reported and retried next run
                failed.append(r["name"])
                print("FAILED", edition, r["name"], str(e)[:200])
    out_path.write_text(json.dumps(done, indent=1))
    print(edition, kind, "compiled", len(done), "of", len(raw), "failed", len(failed))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--editions", nargs="*", default=["2014", "2024"])
    ap.add_argument("--kinds", nargs="*", default=["monsters", "spells"])
    a = ap.parse_args()
    for ed in a.editions:
        for kind in a.kinds:
            run(ed, kind, set(a.only or []), a.workers)
