"""Download SRD 5.1 (2014) and SRD 5.2 (2024) monsters and spells from dnd5eapi.co.

Run once; results are bundled in data/raw so the app never needs the network for rules data.
SRD content is licensed CC-BY-4.0 by Wizards of the Coast.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://www.dnd5eapi.co"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"


def get(path):
    r = requests.get(BASE + path, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch(edition, kind):
    index = get(f"/api/{edition}/{kind}")["results"]
    with ThreadPoolExecutor(8) as pool:
        items = list(pool.map(lambda x: get(x["url"]), index))
    for it in items:
        for k in ("image", "url", "updated_at"):
            it.pop(k, None)
    (OUT / edition).mkdir(parents=True, exist_ok=True)
    (OUT / edition / f"{kind}.json").write_text(json.dumps(items, indent=1))
    print(edition, kind, len(items))


if __name__ == "__main__":
    for ed in ("2014", "2024"):
        for kind in ("monsters", "spells"):
            fetch(ed, kind)
    sys.exit(0)
