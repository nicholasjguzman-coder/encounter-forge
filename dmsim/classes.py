"""Quick-build player characters: class + level -> a reasonable optimized build.

Assumptions (shown in the app): standard array / point buy with the primary ability at 17
(+1 at levels 4 and 8, capping at 20), Con 14, typical starting armor, and the SRD subclass.
Spells come from a curated list of popular combat spells, filtered to what the edition's SRD contains.
"""
import math

from . import subclasses
from .data import find_spell

CLASSES = ["barbarian", "bard", "cleric", "druid", "fighter", "monk",
           "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard"]

SUBCLASS = {
    "barbarian": "Berserker", "bard": "Lore", "cleric": "Life", "druid": "Land", "fighter": "Champion",
    "monk": "Open Hand", "paladin": "Devotion", "ranger": "Hunter", "rogue": "Thief",
    "sorcerer": "Draconic", "warlock": "Fiend", "wizard": "Evoker",
}

FULL_SLOTS = {
    1: [2], 2: [3], 3: [4, 2], 4: [4, 3], 5: [4, 3, 2], 6: [4, 3, 3], 7: [4, 3, 3, 1], 8: [4, 3, 3, 2],
    9: [4, 3, 3, 3, 1], 10: [4, 3, 3, 3, 2], 11: [4, 3, 3, 3, 2, 1], 12: [4, 3, 3, 3, 2, 1],
    13: [4, 3, 3, 3, 2, 1, 1], 14: [4, 3, 3, 3, 2, 1, 1], 15: [4, 3, 3, 3, 2, 1, 1, 1],
    16: [4, 3, 3, 3, 2, 1, 1, 1], 17: [4, 3, 3, 3, 2, 1, 1, 1, 1], 18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1], 20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}
PACT_SLOTS = {lv: (1 if lv == 1 else 2 if lv < 11 else 3 if lv < 17 else 4) for lv in range(1, 21)}

#            hit die, primary, saves, caster type, base AC by tier (1-4, 5-8, 9+)
CLASS_INFO = {
    "barbarian": (12, "str", ("str", "con"), None, (14, 15, 16)),
    "bard": (8, "cha", ("dex", "cha"), "full", (14, 14, 15)),
    "cleric": (8, "wis", ("wis", "cha"), "full", (18, 18, 19)),
    "druid": (8, "wis", ("int", "wis"), "full", (16, 16, 17)),
    "fighter": (10, "str", ("str", "con"), None, (18, 19, 20)),
    "monk": (8, "dex", ("str", "dex"), None, (15, 16, 17)),
    "paladin": (10, "str", ("wis", "cha"), "half", (18, 19, 20)),
    "ranger": (10, "dex", ("str", "dex"), "half", (15, 16, 17)),
    "rogue": (8, "dex", ("dex", "int"), None, (14, 15, 16)),
    "sorcerer": (6, "cha", ("con", "cha"), "full", (15, 15, 16)),
    "warlock": (8, "cha", ("wis", "cha"), "pact", (13, 14, 14)),
    "wizard": (6, "int", ("int", "wis"), "full", (12, 12, 13)),
}

CURATED = {
    "cleric": ["Sacred Flame", "Toll the Dead", "Word of Radiance", "Bless", "Healing Word", "Cure Wounds",
               "Guiding Bolt", "Shield of Faith", "Inflict Wounds", "Spiritual Weapon", "Hold Person", "Aid",
               "Spirit Guardians", "Mass Healing Word", "Banishment", "Guardian of Faith", "Flame Strike",
               "Mass Cure Wounds", "Harm", "Heal", "Blade Barrier", "Fire Storm", "Holy Aura", "Mass Heal"],
    "bard": ["Vicious Mockery", "Dissonant Whispers", "Healing Word", "Faerie Fire", "Thunderwave", "Bane",
             "Hold Person", "Shatter", "Hypnotic Pattern", "Mass Healing Word", "Slow", "Greater Invisibility",
             "Polymorph", "Hold Monster", "Mass Cure Wounds", "Irresistible Dance", "Otto's Irresistible Dance",
             "Mass Suggestion", "Forcecage", "Power Word Stun", "Power Word Kill", "Power Word Heal"],
    "druid": ["Produce Flame", "Thorn Whip", "Starry Wisp", "Healing Word", "Cure Wounds", "Entangle",
              "Thunderwave", "Faerie Fire", "Moonbeam", "Flaming Sphere", "Call Lightning", "Ice Storm",
              "Insect Plague", "Mass Cure Wounds", "Sunbeam", "Fire Storm", "Heal", "Sunburst", "Storm of Vengeance"],
    "sorcerer": ["Fire Bolt", "Ray of Frost", "Chill Touch", "Magic Missile", "Chromatic Orb", "Shield",
                 "Burning Hands", "Scorching Ray", "Hold Person", "Fireball", "Lightning Bolt", "Haste",
                 "Ice Storm", "Banishment", "Cone of Cold", "Hold Monster", "Chain Lightning", "Disintegrate",
                 "Finger of Death", "Delayed Blast Fireball", "Sunburst", "Power Word Stun", "Meteor Swarm"],
    "wizard": ["Fire Bolt", "Ray of Frost", "Magic Missile", "Shield", "Burning Hands", "Thunderwave",
               "Scorching Ray", "Web", "Hold Person", "Fireball", "Hypnotic Pattern", "Lightning Bolt", "Slow",
               "Ice Storm", "Banishment", "Wall of Fire", "Cone of Cold", "Hold Monster", "Chain Lightning",
               "Disintegrate", "Finger of Death", "Delayed Blast Fireball", "Sunburst", "Power Word Stun",
               "Meteor Swarm"],
    "warlock": ["Eldritch Blast", "Hex", "Hellish Rebuke", "Burning Hands", "Scorching Ray", "Hold Person",
                "Fireball", "Hunger of Hadar", "Banishment", "Fire Shield", "Hold Monster", "Flame Strike"],
    "paladin": ["Bless", "Cure Wounds", "Shield of Faith", "Divine Favor", "Aid", "Magic Weapon",
                "Branding Smite", "Crusader's Mantle", "Aura of Vitality", "Banishment", "Destructive Wave"],
    "ranger": ["Hunter's Mark", "Cure Wounds", "Hail of Thorns", "Spike Growth", "Lightning Arrow",
               "Conjure Barrage", "Conjure Volley", "Swift Quiver"],
}

# Resource weights for the "% of resources used" metric (spell slots weigh their level).
RESOURCE_WEIGHTS = {
    "rage": 1.5, "ki": 0.5, "action_surge": 2.0, "second_wind": 1.0, "lay_on_hands": 0.1,
    "channel_divinity": 2.0, "bardic_inspiration": 1.0, "sorcery_points": 0.5, "potion": 1.0,
    "item_charge": 1.0, "ammunition": 0.02, "hunters_mark_free": 1.0,
    "superiority": 1.0, "war_priest": 1.0, "warding_flare": 0.5, "wild_shape": 1.5, "fighting_spirit": 1.0,
    "hexblade_curse": 1.5, "healing_light": 0.3, "bladesong": 1.0, "portent": 1.5, "dread_ambusher": 1.0,
}


def prof_bonus(level):
    return 2 + (level - 1) // 4


def primary_mod(level):
    return 3 + (1 if level >= 4 else 0) + (1 if level >= 8 else 0)


def caster_slots(caster, level, edition):
    if caster == "full":
        return list(FULL_SLOTS[level])
    if caster == "half":
        if level == 1 and edition == "2014":
            return []
        return list(FULL_SLOTS[max(1, math.ceil(level / 2))])
    return []


def _weapon(name, dice, mod, bonus_hit, bonus_dmg, dtype, melee=True, rng=5):
    return {"name": name, "cost": "action", "kind": "attack", "melee": melee, "range": rng, "rolls": 1,
            "attack_bonus": bonus_hit, "damage": [{"dice": f"{dice}+{mod + bonus_dmg}", "type": dtype}],
            "targets": 1, "weapon": True}


def build_pc(edition, cls, level, name=None, subclass=None):
    hd, primary, saves, caster, ac_tiers = CLASS_INFO[cls]
    pb, pm = prof_bonus(level), primary_mod(level)
    con = 2
    mods = {"str": 0, "dex": 2, "con": con, "int": 0, "wis": 1, "cha": 0}
    mods[primary] = pm
    if cls == "paladin":
        mods["cha"] = 2 if level < 8 else 3
    if cls == "monk":
        mods["wis"] = 2 if level < 8 else 3
    if cls in ("cleric", "druid"):
        mods["str"] = 2
    if cls in ("fighter", "barbarian", "paladin"):
        mods["dex"] = 0
    if cls == "ranger":
        mods["wis"] = 2
    ac = ac_tiers[0 if level < 5 else 1 if level < 9 else 2]
    if cls == "monk":
        ac = 10 + mods["dex"] + mods["wis"]
    hp = hd + con + (level - 1) * (hd // 2 + 1 + con)
    if cls == "sorcerer":
        hp += level  # Draconic Resilience
    save_bonus = {a: mods[a] + (pb if a in saves else 0) for a in mods}

    f = {}  # features
    res = {}  # resources: name -> max
    attacks = []
    extra = 1 if level >= 5 and cls in ("barbarian", "fighter", "monk", "paladin", "ranger") else 0
    if cls == "fighter":
        extra = 1 if level >= 5 else 0
        extra += 1 if level >= 11 else 0
        extra += 1 if level >= 20 else 0
    f["extra_attacks"] = extra
    hit = pb + mods[primary]

    if cls == "barbarian":
        attacks.append(_weapon("Greataxe", "1d12", mods["str"], hit, 0, "slashing"))
        res["rage"] = [2, 2, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 99][level - 1]
        if edition == "2024":
            res["rage"] = [2, 2, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6][level - 1]
        f["rage_damage"] = 2 if level < 9 else 3 if level < 16 else 4
        f["reckless"] = level >= 2
        f["frenzy"] = level >= 3
        f["brutal_critical"] = 0 if edition == "2024" else (1 if level >= 9 else 0) + (1 if level >= 13 else 0) + (1 if level >= 17 else 0)
        if edition == "2024":
            f["graze"] = True
        hp += 0
    elif cls == "fighter":
        attacks.append(_weapon("Longsword", "1d8", mods["str"], hit, 2, "slashing"))
        res["second_wind"] = 1 if edition == "2014" else (2 if level < 4 else 3 if level < 10 else 4)
        if level >= 2:
            res["action_surge"] = 2 if level >= 17 else 1
        f["crit"] = 18 if level >= 15 else 19 if level >= 3 else 20
        if edition == "2024":
            f["sap"] = True
    elif cls == "monk":
        die = ({1: "1d4", 5: "1d6", 11: "1d8", 17: "1d10"} if edition == "2014"
               else {1: "1d6", 5: "1d8", 11: "1d10", 17: "1d12"})
        ma = [d for lv, d in die.items() if level >= lv][-1]
        attacks.append(_weapon("Unarmed Strike", ma if ma != "1d4" else "1d6", mods["dex"], hit, 0, "bludgeoning"))
        f["martial_arts_die"] = ma
        if level >= 2:
            res["ki"] = level
        f["flurry_hits"] = 3 if (edition == "2024" and level >= 10) else 2
        f["stunning_strike"] = level >= 5
        f["evasion"] = level >= 7
        f["deflect"] = level >= 3
        f["ki_dc"] = 8 + pb + mods["wis"]
    elif cls == "paladin":
        attacks.append(_weapon("Longsword", "1d8", mods["str"], hit, 2, "slashing"))
        f["smite"] = level >= 2 or edition == "2024"
        f["improved_smite"] = level >= 11
        res["lay_on_hands"] = 5 * level
        if level >= 3:
            res["channel_divinity"] = 1 if (edition == "2014" or level < 11) else 2
            f["sacred_weapon"] = mods["cha"]
        f["aura"] = mods["cha"] if level >= 6 else 0
        f["aura_resist_fear"] = level >= 10
    elif cls == "ranger":
        attacks.append(_weapon("Longbow", "1d8", mods["dex"], hit + 2, 0, "piercing", melee=False, rng=150))
        f["colossus_slayer"] = level >= 3
        res["ammunition"] = 20
        if edition == "2024":
            res["hunters_mark_free"] = max(1, mods["wis"]) if level < 5 else mods["wis"] + 1
    elif cls == "rogue":
        attacks.append(_weapon("Rapier", "1d8", mods["dex"], hit, 0, "piercing"))
        f["sneak_attack"] = f"{math.ceil(level / 2)}d6"
        f["uncanny_dodge"] = level >= 5
        f["evasion"] = level >= 7
        if edition == "2024":
            f["vex"] = True
    elif cls == "bard":
        attacks.append(_weapon("Rapier", "1d8", mods["dex"], pb + mods["dex"], 0, "piercing"))
        res["bardic_inspiration"] = max(1, mods["cha"]) if not (edition == "2024" and level >= 5) else mods["cha"]
        f["inspiration_die"] = "1d6" if level < 5 else "1d8" if level < 10 else "1d10" if level < 15 else "1d12"
        f["cutting_words"] = level >= 3
    elif cls == "cleric":
        attacks.append(_weapon("Mace", "1d6", mods["str"], pb + mods["str"], 0, "bludgeoning"))
        f["disciple_of_life"] = True
        if level >= 2:
            res["channel_divinity"] = (1 if level < 6 else 2 if level < 18 else 3) if edition == "2014" else (2 if level < 6 else 3 if level < 18 else 4)
            f["preserve_life"] = 5 * level
        f["divine_strike"] = ("1d8" if level >= 8 else None) if edition == "2014" else ("1d8" if level >= 7 else None)
        if level >= 14:
            f["divine_strike"] = "2d8"
    elif cls == "druid":
        attacks.append(_weapon("Scimitar", "1d6", mods["dex"], pb + mods["dex"], 0, "slashing"))
    elif cls == "sorcerer":
        attacks.append(_weapon("Dagger", "1d4", mods["dex"], pb + mods["dex"], 0, "piercing"))
        f["elemental_affinity"] = mods["cha"] if level >= 6 else 0
        if level >= 2:
            res["sorcery_points"] = level
    elif cls == "warlock":
        attacks.append(_weapon("Dagger", "1d4", mods["dex"], pb + mods["dex"], 0, "piercing"))
        f["agonizing"] = mods["cha"] if level >= 2 else 0
        f["dark_ones_blessing"] = mods["cha"] + level
    elif cls == "wizard":
        attacks.append(_weapon("Dagger", "1d4", mods["dex"], pb + mods["dex"], 0, "piercing"))
        f["empowered_evocation"] = mods["int"] if level >= 10 else 0
    f["subclass"] = SUBCLASS[cls]

    slots = caster_slots(caster, level, edition) if caster in ("full", "half") else []
    pact = None
    if caster == "pact":
        pact = {"count": PACT_SLOTS[level], "level": min(5, math.ceil(level / 2))}
    max_spell_level = len(slots) if slots else (pact["level"] if pact else 0)
    if cls == "warlock" and level >= 11:
        max_spell_level = 5  # Mystic Arcanum not modelled; pact slots cap at 5

    spells = []
    if caster:
        spell_mod = mods[primary] if cls not in ("paladin",) else mods["cha"]
        if cls == "ranger":
            spell_mod = mods["wis"]
        for nm in CURATED.get(cls, []):
            s = find_spell(edition, nm)
            if not s or s["level"] > max_spell_level:
                continue
            if s["ability"].get("support") == "none":
                continue
            spells.append(s)
        f["spell_mod"] = spell_mod
        f["spell_dc"] = 8 + pb + spell_mod
        f["spell_attack"] = pb + spell_mod

    pc = {
        "name": name or f"{cls.title()} {level}", "cls": cls, "level": level, "edition": edition,
        "hp": hp, "ac": ac, "speed": 40 if cls in ("monk",) and level >= 2 else 30,
        "saves": save_bonus, "mods": mods, "init": mods["dex"], "prof": pb,
        "attacks": attacks, "features": f, "resources": res,
        "slots": slots, "pact": pact, "spells": spells,
    }
    subclasses.apply(pc, edition, subclass)
    return pc


def describe_build(pc):
    """One-line human summary for the UI."""
    bits = [f"HP {pc['hp']}", f"AC {pc['ac']}", f"{pc['features']['subclass']}"]
    if pc["slots"]:
        bits.append("slots " + "/".join(str(s) for s in pc["slots"]))
    if pc["pact"]:
        bits.append(f"{pc['pact']['count']} pact slots (lvl {pc['pact']['level']})")
    if pc["spells"]:
        bits.append(f"{len(pc['spells'])} combat spells")
    return " · ".join(bits)
