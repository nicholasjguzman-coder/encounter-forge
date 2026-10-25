"""Loads bundled SRD data (raw + compiled) and the official encounter-difficulty tables."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITIONS = {"2014": "5e (2014 rules)", "2024": "5.5e (2024 rules)"}

# --- Official difficulty tables -------------------------------------------------------------
# 2014 Basic Rules / DMG: XP thresholds per character (easy, medium, hard, deadly).
XP_THRESHOLDS_2014 = {
    1: (25, 50, 75, 100), 2: (50, 100, 150, 200), 3: (75, 150, 225, 400), 4: (125, 250, 375, 500),
    5: (250, 500, 750, 1100), 6: (300, 600, 900, 1400), 7: (350, 750, 1100, 1700), 8: (450, 900, 1400, 2100),
    9: (550, 1100, 1600, 2400), 10: (600, 1200, 1900, 2800), 11: (800, 1600, 2400, 3600),
    12: (1000, 2000, 3000, 4500), 13: (1100, 2200, 3400, 5100), 14: (1250, 2500, 3800, 5700),
    15: (1400, 2800, 4300, 6400), 16: (1600, 3200, 4800, 7200), 17: (2000, 3900, 5900, 8800),
    18: (2100, 4200, 6300, 9500), 19: (2400, 4900, 7300, 10900), 20: (2800, 5700, 8500, 12700),
}
MULTIPLIERS_2014 = [(1, 1), (2, 1.5), (3, 2), (7, 2.5), (11, 3), (15, 4)]  # (min monsters, multiplier)
MULTIPLIER_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 4, 5]

# 2024 DMG: XP budget per character (low, moderate, high).
XP_BUDGET_2024 = {
    1: (50, 75, 100), 2: (100, 150, 200), 3: (150, 225, 400), 4: (250, 375, 500), 5: (500, 750, 1100),
    6: (600, 1000, 1400), 7: (750, 1300, 1700), 8: (1000, 1700, 2100), 9: (1300, 2000, 2600),
    10: (1600, 2300, 3100), 11: (1900, 2900, 4100), 12: (2200, 3700, 4700), 13: (2600, 4200, 5400),
    14: (2900, 4900, 6200), 15: (3300, 5400, 7800), 16: (3800, 6100, 9800), 17: (4500, 7200, 11700),
    18: (5000, 8700, 14200), 19: (5500, 10700, 17200), 20: (6400, 13200, 22000),
}


def official_difficulty(edition, party_levels, monster_xps):
    """Rate an encounter the way the edition's rules do. Returns a dict for display."""
    total = sum(monster_xps)
    if edition == "2014":
        n = len(monster_xps)
        mult = 1
        for k, m in MULTIPLIERS_2014:
            if n >= k:
                mult = m
        i = MULTIPLIER_STEPS.index(mult)
        if len(party_levels) < 3:
            i = min(i + 1, len(MULTIPLIER_STEPS) - 1)
        elif len(party_levels) >= 6:
            i = max(i - 1, 0)
        mult = MULTIPLIER_STEPS[i] if n else 1
        adjusted = total * mult
        th = [sum(XP_THRESHOLDS_2014[lv][j] for lv in party_levels) for j in range(4)]
        names = ["Easy", "Medium", "Hard", "Deadly"]
        rating = "Trivial"
        for name, t in zip(names, th):
            if adjusted >= t:
                rating = name
        return {"rating": rating, "total_xp": total, "multiplier": mult, "adjusted_xp": adjusted,
                "thresholds": dict(zip(names, th)), "basis": "2014 Basic Rules XP thresholds with group multiplier"}
    th = [sum(XP_BUDGET_2024[lv][j] for lv in party_levels) for j in range(3)]
    names = ["Low", "Moderate", "High"]
    rating = "Below Low"
    for name, t in zip(names, th):
        if total >= t:
            rating = name
    if th and total > th[2]:
        rating = "Above High"
    return {"rating": rating, "total_xp": total, "multiplier": 1, "adjusted_xp": total,
            "thresholds": dict(zip(names, th)), "basis": "2024 DMG XP budget (no group multiplier)"}


def targets_in_area(area, max_targets):
    """DMG 'Targets in Areas of Effect' guideline."""
    if not area:
        return 1
    shape, size = area.get("shape", "sphere"), max(int(area.get("size", 20)), 5)
    if shape == "cone":
        n = size / 10
    elif shape in ("cube", "square"):
        n = size / 5
    elif shape == "line":
        n = size / 30
    else:  # sphere, cylinder, emanation, circle (radius)
        n = size / 5
    return max(1, min(max_targets, math.ceil(n)))


# --- Raw + compiled loading -----------------------------------------------------------------
def _speed(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else 0


def _types(entries):
    """Parse resistance strings like 'bludgeoning, piercing, and slashing from nonmagical attacks'."""
    types, nonmagical = set(), set()
    for e in entries or []:
        s = (e if isinstance(e, str) else e.get("name", "")).lower()
        found = re.findall(r"acid|bludgeoning|cold|fire|force|lightning|necrotic|piercing|poison|psychic|radiant|slashing|thunder", s)
        if "nonmagical" in s or "non-magical" in s or "aren't silvered" in s or "not silvered" in s:
            nonmagical.update(found)
        else:
            types.update(found)
    return types, nonmagical


CUSTOM_DIR = ROOT / "data" / "custom"
CR_XP = {0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800, 6: 2300, 7: 2900, 8: 3900,
         9: 5000, 10: 5900, 11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000, 17: 18000, 18: 20000,
         19: 22000, 20: 25000, 21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000, 26: 90000, 27: 105000,
         28: 120000, 29: 135000, 30: 155000}


def cr_prof(cr):
    return 2 + max(0, (int(max(cr, 1)) - 1) // 4)


def _custom(edition):
    p = CUSTOM_DIR / f"{edition}.json"
    return json.loads(p.read_text()) if p.exists() else {}


@lru_cache(maxsize=None)
def raw(edition, kind):
    out = {x["index"]: x for x in json.loads((ROOT / "data/raw" / edition / f"{kind}.json").read_text())}
    if kind == "monsters":
        out.update({i: c["raw"] for i, c in _custom(edition).items()})
    return out


@lru_cache(maxsize=None)
def compiled(edition, kind):
    p = ROOT / "data/compiled" / edition / f"{kind}.json"
    out = json.loads(p.read_text()) if p.exists() else {}
    if kind == "monsters":
        out.update({i: c["combat"] for i, c in _custom(edition).items()})
    return out


def is_custom(index):
    return str(index).startswith("custom-")


def custom_creatures(edition):
    return _custom(edition)


def save_custom(edition, index, raw_entry, combat):
    """Persist a custom creature and make it available immediately."""
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    allc = _custom(edition)
    allc[index] = {"raw": raw_entry, "combat": combat}
    (CUSTOM_DIR / f"{edition}.json").write_text(json.dumps(allc, indent=1))
    raw(edition, "monsters")[index] = raw_entry
    compiled(edition, "monsters")[index] = combat


def delete_custom(edition, index):
    allc = _custom(edition)
    allc.pop(index, None)
    (CUSTOM_DIR / f"{edition}.json").write_text(json.dumps(allc, indent=1))
    raw(edition, "monsters").pop(index, None)
    compiled(edition, "monsters").pop(index, None)


def monster_names(edition):
    """Monsters available for selection: compiled ones only, sorted by name."""
    comp = compiled(edition, "monsters")
    r = raw(edition, "monsters")
    return sorted(((r[i]["name"], i, r[i].get("challenge_rating", 0)) for i in comp if i in r), key=lambda x: x[0])


def monster_base(edition, index):
    """Numbers read straight from the SRD data by code (never by the LLM)."""
    m = raw(edition, "monsters")[index]
    ac = m.get("armor_class") or [{"value": 10}]
    ac = ac[0].get("value", 10) if isinstance(ac, list) else int(ac)
    scores = {a[:3]: m.get(a, 10) for a in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")}
    saves = {k: (v - 10) // 2 for k, v in scores.items()}
    for p in m.get("proficiencies", []):
        idx = p["proficiency"].get("index", "")
        if idx.startswith("saving-throw-"):
            saves[idx[-3:]] = p["value"]
    sp = m.get("speed") or {}
    resist, resist_nm = _types(m.get("damage_resistances"))
    immune, immune_nm = _types(m.get("damage_immunities"))
    vuln, _ = _types(m.get("damage_vulnerabilities"))
    cond_imm = {(c if isinstance(c, str) else c.get("index", c.get("name", ""))).lower() for c in m.get("condition_immunities", [])}
    return {
        "index": index, "name": m["name"], "type": str(m.get("type", "")).lower(), "size": m.get("size", "Medium"),
        "cr": m.get("challenge_rating", 0), "xp": m.get("xp", 0), "ac": int(ac), "hp": int(m.get("hit_points", 1)),
        "speed": max(_speed(sp.get("walk")), _speed(sp.get("fly")), _speed(sp.get("swim")) // 2, 5),
        "scores": scores, "saves": saves, "init": (scores["dex"] - 10) // 2,
        "resist": resist, "resist_nonmagical": resist_nm, "immune": immune, "immune_nonmagical": immune_nm,
        "vuln": vuln, "condition_immune": cond_imm, "prof": m.get("proficiency_bonus", 2),
    }


def spell_library(edition):
    """Compiled spells keyed by index, with level/classes merged in from the raw data."""
    comp = compiled(edition, "spells")
    out = {}
    for idx, r in raw(edition, "spells").items():
        if idx not in comp:
            continue
        out[idx] = {
            "index": idx, "name": r["name"], "level": r.get("level", 0),
            "school": (r.get("school") or {}).get("name", ""),
            "classes": [c["index"] for c in r.get("classes", [])],
            "ability": comp[idx]["ability"],
        }
    return out


def find_spell(edition, name):
    lib = spell_library(edition)
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if key in lib:
        return lib[key]
    for s in lib.values():
        if s["name"].lower() == name.lower():
            return s
    return None
