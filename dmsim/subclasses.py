"""Subclass options beyond the SRD subclass.

These are our own encodings of each subclass's combat mechanics (numbers and short labels only, no rules
text). Dungeons & Dragons and its subclasses are trademarks/content of Wizards of the Coast; only the
SRD subclasses are covered by the CC-BY-4.0 SRD license. Mechanics are simplified to what matters in a
one-dimensional combat simulation; see NOTES for what each option models.
"""
from .data import find_spell

# edition -> class -> [subclass names]. The first entry of each class is the SRD default (see classes.SUBCLASS).
SUBCLASSES = {
    "2014": {
        "barbarian": ["Berserker", "Totem Warrior (Bear)", "Zealot"],
        "bard": ["Lore", "Valor", "Swords"],
        "cleric": ["Life", "War", "Light", "Twilight"],
        "druid": ["Land", "Stars"],
        "fighter": ["Champion", "Battle Master", "Samurai"],
        "monk": ["Open Hand", "Mercy"],
        "paladin": ["Devotion", "Vengeance", "Ancients"],
        "ranger": ["Hunter", "Gloom Stalker", "Fey Wanderer"],
        "rogue": ["Thief", "Assassin", "Swashbuckler"],
        "sorcerer": ["Draconic", "Divine Soul"],
        "warlock": ["Fiend", "Hexblade", "Celestial"],
        "wizard": ["Evoker", "Bladesinging", "Divination"],
    },
    "2024": {
        "barbarian": ["Berserker", "Wild Heart (Bear)", "Zealot"],
        "bard": ["Lore", "Valor"],
        "cleric": ["Life", "War", "Light"],
        "druid": ["Land", "Stars"],
        "fighter": ["Champion", "Battle Master"],
        "monk": ["Open Hand", "Mercy"],
        "paladin": ["Devotion", "Vengeance", "Ancients"],
        "ranger": ["Hunter", "Gloom Stalker", "Fey Wanderer"],
        "rogue": ["Thief", "Assassin"],
        "sorcerer": ["Draconic"],
        "warlock": ["Fiend", "Celestial"],
        "wizard": ["Evoker", "Bladesinging", "Divination"],
    },
}

NOTES = {
    "Totem Warrior (Bear)": "While raging, resistance to all damage except psychic.",
    "Wild Heart (Bear)": "While raging, resistance to all damage except force, necrotic, psychic and radiant.",
    "Zealot": "Divine Fury: first hit each turn while raging deals +1d6 + half barbarian level radiant.",
    "Valor": "Medium armor and shield (+2 AC); Extra Attack at level 6.",
    "Swords": "Extra Attack at 6; Blade Flourish spends Bardic Inspiration for extra damage on a hit.",
    "War": "War Priest: bonus-action weapon attack (Wisdom modifier uses).",
    "Light": "Warding Flare reaction imposes disadvantage on an attack; Radiance of the Dawn (Channel Divinity) area damage.",
    "Twilight": "Twilight Sanctuary (Channel Divinity): allies gain 1d6 + cleric level temp HP each round.",
    "Stars": "Starry Form (Archer): bonus-action ranged spell attack each turn, 1d8 (2d8 at 10) + Wis radiant.",
    "Battle Master": "Superiority dice (d8, d10 at 10, d12 at 18): extra damage and Trip Attack (prone) on hits.",
    "Samurai": "Fighting Spirit: advantage on weapon attacks for the turn plus temp HP (3 uses).",
    "Mercy": "Hand of Harm: once per turn, 1 ki/focus for + martial arts die + Wis necrotic on an unarmed hit.",
    "Vengeance": "Vow of Enmity (Channel Divinity): advantage on attacks against one enemy.",
    "Ancients": "Aura of Warding (level 7): you and allies resist damage from spells.",
    "Gloom Stalker": "Dread Ambusher: +Wis initiative; extra damage on the first turn (2014: extra attack +1d8; 2024: 2d6 psychic, Wis uses).",
    "Fey Wanderer": "Dreadful Strikes: +1d4 psychic once per turn on a weapon hit (1d6 at 11).",
    "Assassin": "Advantage against creatures that haven't acted yet; 2014: crits on surprised foes; 2024: +rogue level damage in round 1.",
    "Swashbuckler": "Rakish Audacity: Sneak Attack in a one-on-one melee without an ally adjacent; +Cha initiative.",
    "Divine Soul": "Cleric spells added (Healing Word, Cure Wounds, Bless, Guiding Bolt, Spirit Guardians...).",
    "Hexblade": "Melee with Charisma, medium armor and shield, Extra Attack at 5, Hexblade's Curse (+prof damage, crit on 19).",
    "Celestial": "Healing Light dice pool (bonus-action heal); Radiant Soul +Cha to fire and radiant spell damage at 6.",
    "Bladesinging": "Bladesong: + Int to AC (proficiency-bonus uses); Extra Attack at 6 with a rapier.",
    "Divination": "Portent: turn an enemy's successful save against your spell into a failure (2 per day, 3 at 14).",
}

DIVINE_SOUL_SPELLS = ["Healing Word", "Cure Wounds", "Bless", "Guiding Bolt", "Spiritual Weapon", "Spirit Guardians",
                      "Mass Healing Word", "Guardian of Faith", "Mass Cure Wounds", "Heal"]
CELESTIAL_SPELLS = ["Sacred Flame", "Cure Wounds", "Guiding Bolt", "Flaming Sphere", "Daylight", "Guardian of Faith",
                    "Flame Strike"]

SUBCLASS_LEVEL_2014 = {"barbarian": 3, "bard": 3, "cleric": 1, "druid": 2, "fighter": 3, "monk": 3, "paladin": 3,
                       "ranger": 3, "rogue": 3, "sorcerer": 1, "warlock": 1, "wizard": 2}


def options(edition, cls):
    return SUBCLASSES[edition][cls]


def subclass_level(edition, cls):
    return SUBCLASS_LEVEL_2014[cls] if edition == "2014" else 3


def _weapon(name, dice, mod, hit, dtype, melee=True, rng=5):
    return {"name": name, "cost": "action", "kind": "attack", "melee": melee, "range": rng, "rolls": 1,
            "attack_bonus": hit, "damage": [{"dice": f"{dice}+{mod}", "type": dtype}], "targets": 1, "weapon": True}


def _add_spells(pc, edition, names):
    have = {s["name"] for s in pc["spells"]}
    top = len(pc["slots"]) if pc["slots"] else (pc["pact"]["level"] if pc["pact"] else 0)
    for nm in names:
        s = find_spell(edition, nm)
        if s and nm not in have and s["level"] <= top and s["ability"].get("support") != "none":
            pc["spells"].append(s)


def apply(pc, edition, name):
    """Mutate a quick-build PC dict to use the given subclass. Returns the subclass actually applied."""
    cls, lv = pc["cls"], pc["level"]
    if not name or name not in SUBCLASSES[edition][cls] or name == SUBCLASSES[edition][cls][0]:
        return pc["features"]["subclass"]
    f, res, mods, pb = pc["features"], pc["resources"], pc["mods"], pc["prof"]
    # Remove SRD-subclass features that the new subclass doesn't have.
    for k in ("frenzy", "cutting_words", "disciple_of_life", "preserve_life", "sacred_weapon", "colossus_slayer",
              "elemental_affinity", "dark_ones_blessing", "empowered_evocation"):
        f.pop(k, None)
    if cls == "fighter":
        f["crit"] = 20
    if cls == "sorcerer":  # Draconic Resilience
        pc["hp"] -= lv
        pc["ac"] = 12
    f["subclass"] = name
    if lv < subclass_level(edition, cls):
        return name
    wis = max(1, mods["wis"])

    if name in ("Totem Warrior (Bear)", "Wild Heart (Bear)"):
        f["rage_resist_all"] = ["psychic"] if edition == "2014" else ["force", "necrotic", "psychic", "radiant"]
    elif name == "Zealot":
        f["divine_fury"] = True
    elif name == "Valor":
        pc["ac"] += 2
        if lv >= 6:
            f["extra_attacks"] = 1
    elif name == "Swords":
        f["blade_flourish"] = True
        if lv >= 6:
            f["extra_attacks"] = 1
    elif name == "War":
        res["war_priest"] = wis
    elif name == "Light":
        res["warding_flare"] = wis
        f["radiance_of_dawn"] = lv >= 2
    elif name == "Twilight":
        f["twilight_sanctuary"] = lv >= 2
    elif name == "Stars":
        res["wild_shape"] = 2 if edition == "2014" else (2 if lv < 6 else 3 if lv < 17 else 4)
        f["starry_archer"] = "2d8" if lv >= 10 else "1d8"
    elif name == "Battle Master":
        res["superiority"] = 4 if lv < 7 else 5 if lv < 15 else 6
        f["sup_die"] = "1d8" if lv < 10 else "1d10" if lv < 18 else "1d12"
        f["maneuver_dc"] = 8 + pb + mods["str"]
    elif name == "Samurai":
        res["fighting_spirit"] = 3
        f["fighting_spirit_thp"] = 5 if lv < 10 else 10 if lv < 15 else 15
    elif name == "Mercy":
        f["hand_of_harm"] = True
    elif name == "Vengeance":
        f["vow_of_enmity"] = True
    elif name == "Ancients":
        f["aura_warding"] = lv >= 7
    elif name == "Gloom Stalker":
        pc["init"] += mods["wis"]
        f["dread_ambusher"] = True
        if edition == "2024":
            res["dread_ambusher"] = wis
    elif name == "Fey Wanderer":
        f["dreadful_strikes"] = "1d6" if lv >= 11 else "1d4"
    elif name == "Assassin":
        f["assassinate"] = True
    elif name == "Swashbuckler":
        mods["cha"] = 2
        pc["init"] += 2
        f["rakish"] = True
    elif name == "Divine Soul":
        _add_spells(pc, edition, DIVINE_SOUL_SPELLS)
    elif name == "Hexblade":
        hit = pb + mods["cha"]
        pc["attacks"] = [_weapon("Longsword (Hex Warrior)", "1d8", mods["cha"], hit, "slashing")]
        pc["ac"] = 17
        if lv >= 5:
            f["extra_attacks"] = 1
        res["hexblade_curse"] = 1
        f["hex_curse_bonus"] = pb
    elif name == "Celestial":
        res["healing_light"] = 1 + lv
        f["healing_light_max"] = max(1, mods["cha"])
        f["radiant_soul"] = mods["cha"] if lv >= 6 else 0
        _add_spells(pc, edition, CELESTIAL_SPELLS)
    elif name == "Bladesinging":
        res["bladesong"] = pb
        f["bladesong_ac"] = mods["int"]
        pc["attacks"] = [_weapon("Rapier", "1d8", mods["dex"], pb + mods["dex"], "piercing")]
        if lv >= 6:
            f["extra_attacks"] = 1
    elif name == "Divination":
        res["portent"] = 3 if lv >= 14 else 2
    return name
