"""Monte Carlo combat simulator.

Pure Python, no model calls: dice, hit chances, saves, conditions, concentration, legendary actions,
death saves and resource use are all resolved in code. Agents only decide the *settings* (tactics,
item effects, encounter changes); this module produces the numbers.

Simplifications (documented in the app): one-dimensional positioning (each side starts on a line,
a set distance apart), no opportunity attacks, no terrain or cover, area spells hit the number of
targets suggested by the DMG "Targets in Areas of Effect" table.
"""
import copy
import random
from dataclasses import dataclass, field

from . import data
from .classes import RESOURCE_WEIGHTS, build_pc
from .dice import avg, d20, dice_avg, roll, scale

CANT_ACT = {"paralyzed", "stunned", "unconscious", "petrified", "incapacitated", "banished", "charmed"}
GIVE_ADV = {"paralyzed", "stunned", "unconscious", "restrained", "blinded", "petrified", "outlined"}
ATTACK_DISADV = {"blinded", "frightened", "poisoned", "restrained", "prone"}
AUTO_FAIL_STR_DEX = {"paralyzed", "stunned", "unconscious", "petrified"}
PHYSICAL = {"bludgeoning", "piercing", "slashing"}
TYPE_ONLY = {"hold person": "humanoid", "charm person": "humanoid", "dominate person": "humanoid"}


@dataclass
class Tactics:
    monster_targeting: str = "random"  # random | focus_weakest | focus_casters | nearest
    party_targeting: str = "focus_weakest"  # focus_weakest | focus_strongest | random
    finish_downed: bool = False  # monsters attack PCs at 0 HP
    flee_at: float = 0.0  # monsters flee below this fraction of HP (0 = fight to the death)
    per_monster: dict = field(default_factory=dict)  # monster index -> {"targeting": str, "flee_at": float}
    resource_policy: str = "balanced"  # conserve | balanced | nova


@dataclass
class Settings:
    edition: str = "2014"
    start_rounds: int = 1  # rounds a speed-30 creature needs (dashing) to reach melee
    surprise: str = "none"  # none | party | monsters (the side that is surprised)
    max_rounds: int = 20
    tactics: Tactics = field(default_factory=Tactics)
    item_effects: list = field(default_factory=list)  # validated modifiers from the Items agent
    allies: list = field(default_factory=list)  # [{"index": monster id, "count": n}] fighting for the party


class C:
    """A combatant (PC or monster). Attributes are plain fields for speed."""

    def __init__(self, name, side, pc=None):
        self.name, self.side = name, side
        self.friendly = side == "party"  # on the players' side (PCs and allied creatures)
        self.is_pc = self.friendly if pc is None else pc  # uses player-character rules (death saves, class features)
        self.conditions = {}  # name -> dict(rounds, save, dc, source_effect, breaks)
        self.buffs = []  # (Buff dict, effect)
        self.effects = []  # effects this creature owns (concentration etc.)
        self.concentration = None
        self.temp_hp = 0
        self.dead = self.fled = self.stable = False
        self.ds_succ = self.ds_fail = 0
        self.dropped = False
        self.reaction = True
        self.uses = {}  # ability name -> remaining uses
        self.recharge_ready = {}
        self.res_used = {}
        self.x = 0
        self.raging = 0
        self.legendary_points = 0
        self.legendary_max = 0
        self.legendary = []
        self.sneak_used = False
        self.vex_target = None
        self.sapped = False
        self.reckless_until = -1
        self.magic_weapons = False
        self.potions = []
        self.slay = []
        self.item_spells = []
        self.adv_saves = False
        self.dmg_bonus_items = []
        self.attack_bonus_items = 0
        self.save_bonus_items = 0
        self.damage_taken = 0
        self.killed_by = None
        self.cls, self.level, self.tier, self.prof = None, 0, 1, 2
        self.mods = {}
        self.spells, self.item_spells = [], []
        self.resources, self.resources_max = {}, {}
        self.features = {}
        self.size = "Medium"
        self.acted = False
        self.cursed = self.vow_target = None
        self.twilight = self.starry = 0

    # -- state helpers --
    @property
    def up(self):
        return self.hp > 0 and not self.dead and not self.fled and "banished" not in self.conditions

    @property
    def present(self):
        return not self.dead and not self.fled and "banished" not in self.conditions

    def can_act(self):
        return self.up and not (CANT_ACT & self.conditions.keys())


# ----------------------------------------------------------------------------------------------
# Building combatants
# ----------------------------------------------------------------------------------------------
def _ability_from_spell(spell, caster_dc, caster_attack):
    a = copy.deepcopy(spell["ability"])
    a["spell_level"] = spell["level"]
    a["school"] = spell.get("school", "")
    a["is_spell"] = True
    if a.get("attack_bonus") is None:
        a["attack_bonus"] = caster_attack
    if a.get("save"):
        if a["save"].get("dc") is None:
            a["save"]["dc"] = caster_dc
    rec = a.get("recurring")
    if rec and rec.get("save") and rec["save"].get("dc") is None:
        rec["save"]["dc"] = caster_dc
    if a.get("condition") and not a.get("save") and a.get("kind") in ("control", "debuff"):
        a["save"] = {"ability": a["condition"].get("save_ability") or "wis", "dc": caster_dc, "half": False}
    return a


def make_monster(edition, index, label, side="enemies"):
    base = data.monster_base(edition, index)
    comp = copy.deepcopy(data.compiled(edition, "monsters")[index])
    c = C(label, side, pc=False)
    c.index, c.cr, c.xp, c.ctype = index, base["cr"], base["xp"], base["type"]
    c.size = base["size"]
    c.max_hp = c.hp = base["hp"]
    c.ac, c.speed, c.saves, c.init = base["ac"], base["speed"], base["saves"], base["init"]
    c.resist, c.immune, c.vuln = set(base["resist"]), set(base["immune"]), set(base["vuln"])
    c.resist_nm, c.immune_nm = set(base["resist_nonmagical"]), set(base["immune_nonmagical"])
    c.cond_immune = base["condition_immune"]
    t = comp.get("traits", {})
    c.traits = t
    c.legendary_resistance = int(t.get("legendary_resistance", 0) or 0)
    c.features = {}
    c.actions = [a for a in comp.get("actions", []) if a.get("support") != "none"]
    c.bonus_actions = [a for a in comp.get("bonus_actions", []) if a.get("support") != "none"]
    c.reactions = comp.get("reactions", [])
    c.multiattack = comp.get("multiattack", [])
    c.legendary_max = int(comp.get("legendary_per_round", 0) or 0)
    c.legendary = comp.get("legendary_actions", [])
    raw_la = {x.get("name", "").lower(): x.get("desc", "") for x in data.raw(edition, "monsters")[index].get("legendary_actions") or []}
    for la in c.legendary:
        nm = ((la.get("ability") or {}).get("name") or la.get("uses_action") or "").lower()
        la["once_per_round"] = "again until" in raw_la.get(nm, "")
    for a in c.actions + c.bonus_actions:
        if "advantage" in (a.get("notes") or "").lower() and len(a.get("damage", [])) > 1:
            a["damage"][-1]["adv_only"] = True
    # Monster spellcasting: resolve spell names against the compiled spell library.
    dc = comp.get("spell_dc") or (8 + base["prof"] + max((base["scores"][k] - 10) // 2 for k in ("int", "wis", "cha")))
    atk = comp.get("spell_attack") or (dc - 8)
    c.spell_dc, c.spell_attack = dc, atk
    c.slots = {int(k): v for k, v in (comp.get("spell_slots") or {}).items()}
    c.slots_max = dict(c.slots)
    c.cantrip_tier = 1 + (c.cr >= 5) + (c.cr >= 11) + (c.cr >= 17)
    names = {a["name"].lower() for a in c.actions}
    for sp in comp.get("spells", []):
        lib = data.find_spell(edition, sp["name"])
        if not lib or lib["ability"].get("support") == "none" or sp["name"].lower() in names:
            continue
        a = _ability_from_spell(lib, dc, atk)
        if a.get("cantrip_scaling"):
            a["damage"] = [{"dice": scale(d["dice"], c.cantrip_tier), "type": d["type"]} for d in a.get("damage", [])]
        a["mon_spell_uses"] = sp["uses"]
        if sp["uses"] == "per_day":
            c.uses[a["name"]] = sp.get("times", 1)
        a["spell_level"] = max(lib["level"], sp.get("level", lib["level"]) if sp["uses"] != "slot" else lib["level"])
        (c.bonus_actions if a.get("cost") == "bonus" else c.actions).append(a)
    for a in c.actions + c.bonus_actions:
        u = a.get("uses") or {}
        if u.get("type") in ("per_day", "per_rest") and a["name"] not in c.uses:
            c.uses[a["name"]] = u.get("times", 1)
        if u.get("type") == "recharge":
            c.recharge_ready[a["name"]] = True
    c.max_slots = {}
    c.resources_max = {}
    return c


def make_pc(edition, cls, level, label, subclass=None):
    b = build_pc(edition, cls, level, label, subclass=subclass)
    c = C(label, "party")
    c.cls, c.level, c.build = cls, level, b
    c.max_hp = c.hp = b["hp"]
    c.ac, c.speed, c.saves, c.init = b["ac"], b["speed"], dict(b["saves"]), b["init"]
    c.resist, c.immune, c.vuln, c.resist_nm, c.immune_nm = set(), set(), set(), set(), set()
    c.cond_immune = set()
    c.traits = {}
    c.legendary_resistance = 0
    c.features = dict(b["features"])
    c.prof = b["prof"]
    c.mods = b["mods"]
    c.ctype = "humanoid"
    c.cr = level / 4
    c.actions = [dict(a) for a in b["attacks"]]
    c.bonus_actions = []
    c.multiattack = []
    c.slots = {i + 1: n for i, n in enumerate(b["slots"])}
    c.pact = dict(b["pact"]) if b["pact"] else None
    if c.pact:
        c.slots = {c.pact["level"]: c.pact["count"]}
    c.max_slots = dict(c.slots)
    c.resources_max = dict(b["resources"])
    c.resources = dict(b["resources"])
    c.spell_dc = c.features.get("spell_dc", 10)
    c.spell_attack = c.features.get("spell_attack", 0)
    c.tier = 1 + (level >= 5) + (level >= 11) + (level >= 17)
    c.spells = []
    for s in b["spells"]:
        a = _ability_from_spell(s, c.spell_dc, c.spell_attack)
        if a.get("cantrip_scaling") and s["level"] == 0:
            a["damage"] = [{"dice": scale(d["dice"], c.tier), "type": d["type"]} for d in a.get("damage", [])]
        if s["name"] == "Eldritch Blast":
            a["rolls"] = c.tier
            a["damage"] = [{"dice": "1d10", "type": "force"}]
            if c.features.get("agonizing"):
                a["damage"] = [{"dice": f"1d10+{c.features['agonizing']}", "type": "force"}]
        c.spells.append(a)
    return c


def apply_item_effects(combatants, effects, rng_label=""):
    """Apply validated modifiers from the Items & Effects agent."""
    for e in effects:
        who = e.get("applies_to", "party")
        if e["type"] == "healing_potions" and who in ("party", "enemies"):
            side = [c for c in combatants if c.is_pc == (who == "party")]
            for k in range(e.get("count", 1)):  # a shared stash: hand potions out one at a time
                if side:
                    c = side[k % len(side)]
                    c.potions.append(e.get("dice", "2d4+2"))
                    c.resources_max["potion"] = c.resources_max.get("potion", 0) + 1
                    c.resources["potion"] = c.resources_max["potion"]
            continue
        targets = [c for c in combatants if (
            (who == "party" and c.is_pc) or (who == "enemies" and not c.friendly) or
            (who not in ("party", "enemies") and c.name.lower() == who.lower()))]
        if not targets and who not in ("party", "enemies"):
            targets = [c for c in combatants if c.is_pc and who.lower() in c.name.lower()]
        t = e["type"]
        for c in targets:
            if t == "attack_bonus":
                c.attack_bonus_items += e["value"]
            elif t == "save_bonus":
                c.save_bonus_items += e["value"]
            elif t == "ac_bonus":
                c.ac += e["value"]
            elif t == "magic_weapons":
                c.magic_weapons = True
            elif t == "damage_bonus":
                c.dmg_bonus_items.append(e)
            elif t == "resistance":
                c.resist |= set(e.get("damage_types", []))
            elif t == "max_hp_bonus":
                c.max_hp += e["value"]
                c.hp += e["value"]
            elif t == "temp_hp":
                c.temp_hp = max(c.temp_hp, e["value"])
            elif t == "advantage_saves":
                c.adv_saves = True
            elif t == "healing_potions":
                for _ in range(e.get("count", 1)):
                    c.potions.append(e.get("dice", "2d4+2"))
                c.resources_max["potion"] = c.resources_max.get("potion", 0) + e.get("count", 1)
                c.resources["potion"] = c.resources_max["potion"]
            elif t == "slay":
                c.slay.append(dict(e))
                c.resources_max["item_charge"] = c.resources_max.get("item_charge", 0) + e.get("uses", 1)
                c.resources["item_charge"] = c.resources_max["item_charge"]
            elif t == "spell":
                lib = data.find_spell(c.build["edition"] if c.is_pc else "2014", e["name"]) if c.is_pc else None
                if lib:
                    a = _ability_from_spell(lib, e.get("dc") or c.spell_dc or 13, e.get("attack") or c.spell_attack or 5)
                    a["item_uses"] = e.get("uses", 1)
                    a["name"] = f"{lib['name']} (item)"
                    c.item_spells.append(a)
                    c.resources_max["item_charge"] = c.resources_max.get("item_charge", 0) + e.get("uses", 1)
                    c.resources["item_charge"] = c.resources_max["item_charge"]


# ----------------------------------------------------------------------------------------------
# The battle
# ----------------------------------------------------------------------------------------------
class Battle:
    def __init__(self, party, enemies, settings, rng, log=False):
        self.party, self.enemies, self.s, self.rng = party, enemies, settings, rng
        self.all = party + enemies
        self.round = 0
        self.log_on = log
        self.lines = []
        self.t = settings.tactics
        self.ed = settings.edition

    def log(self, msg):
        if self.log_on:
            self.lines.append(f"R{self.round}: {msg}")

    # -- geometry --
    def foes(self, c):
        return [x for x in (self.enemies if c.friendly else self.party) if x.present]

    def allies(self, c):
        return [x for x in (self.party if c.friendly else self.enemies) if x.present]

    @staticmethod
    def dist(a, b):
        return abs(a.x - b.x)

    def adjacent_enemy(self, c):
        return any(f.up and self.dist(c, f) <= 5 for f in self.foes(c))

    def move_toward(self, c, target, budget, reach=5):
        gap = self.dist(c, target) - reach
        if gap <= 0:
            return 0
        step = min(budget, gap)
        c.x += step if target.x > c.x else -step
        return step

    # -- rolls --
    def save_bonus(self, c, ability):
        b = c.saves.get(ability, 0) + c.save_bonus_items
        for buff, _ in c.buffs:
            if buff.get("save_bonus"):
                b += roll(self.rng, buff["save_bonus"])
            if buff.get("save_penalty"):
                b -= roll(self.rng, buff["save_penalty"])
        if c.friendly:
            aura = max([p.features.get("aura", 0) for p in self.party if p.up and p.cls == "paladin"] or [0])
            b += aura
        if "slowed" in c.conditions and ability == "dex":
            b -= 2
        return b

    def make_save(self, c, ability, dc, is_spell=False, important=True):
        if ability in ("str", "dex") and AUTO_FAIL_STR_DEX & c.conditions.keys():
            ok = False
        else:
            adv = 1 if (is_spell and c.traits.get("magic_resistance")) or c.adv_saves else 0
            if ability == "dex" and "restrained" in c.conditions:
                adv -= 1
            ok = d20(self.rng, max(-1, min(1, adv))) + self.save_bonus(c, ability) >= dc
        if not ok and important and c.legendary_resistance > 0:
            c.legendary_resistance -= 1
            self.log(f"{c.name} uses Legendary Resistance")
            return True
        return ok

    def ac_of(self, c):
        ac = c.ac
        for buff, _ in c.buffs:
            ac += buff.get("ac", 0) or 0
        if "slowed" in c.conditions:
            ac -= 2
        return ac

    def attack_adv(self, att, tgt, melee):
        adv = dis = False
        if any(b.get("advantage_attacks") for b, _ in att.buffs):
            adv = True
        if GIVE_ADV & tgt.conditions.keys():
            adv = True
        if "prone" in tgt.conditions:
            adv, dis = (True, dis) if melee and self.dist(att, tgt) <= 5 else (adv, True)
        if tgt.reckless_until == self.round and melee:
            adv = True
        if att.traits.get("pack_tactics") and any(a is not att and a.up and self.dist(a, tgt) <= 5 for a in self.allies(att)):
            adv = True
        if att.is_pc and att.features.get("reckless") and melee and att.raging:
            adv = True
        if att.vex_target is tgt or (att.vow_target is tgt and tgt.up):
            adv = True
        if att.features.get("assassinate") and self.round == 1 and not tgt.acted:
            adv = True
        if ATTACK_DISADV & att.conditions.keys():
            dis = True
        if not melee and self.adjacent_enemy(att):
            dis = True
        if att.sapped:
            dis = True
        return (1 if adv else 0) - (1 if dis else 0)

    # -- damage --
    def apply_damage(self, tgt, amount, dtype, src=None, crit=False, magical=True, melee=False, spell=False):
        if amount <= 0 or tgt.dead:
            return 0
        if dtype in tgt.immune or (not magical and dtype in tgt.immune_nm):
            return 0
        if dtype in tgt.resist or (not magical and dtype in tgt.resist_nm) or \
                any(dtype in (b.get("resistances") or []) for b, _ in tgt.buffs) or \
                self.rage_resists(tgt, dtype) or (spell and tgt.friendly and self.warded()):
            amount //= 2
        if dtype in tgt.vuln:
            amount *= 2
        if tgt.temp_hp:
            soak = min(tgt.temp_hp, amount)
            tgt.temp_hp -= soak
            amount -= soak
        if amount <= 0:
            return 0
        tgt.damage_taken += amount
        # Conditions that end when the creature takes damage (Sleep, Hypnotic Pattern).
        for name in [n for n, cd in tgt.conditions.items() if cd.get("breaks")]:
            tgt.conditions.pop(name, None)
        if tgt.hp <= 0 and tgt.is_pc:
            tgt.ds_fail += 2 if crit else 1
            tgt.stable = False
            if amount >= tgt.max_hp or tgt.ds_fail >= 3:
                self.kill(tgt, src)
            return amount
        tgt.hp -= amount
        if tgt.concentration and tgt.hp > 0:
            if not self.make_save(tgt, "con", max(10, amount // 2), important=False):
                self.end_concentration(tgt)
        if tgt.hp <= 0:
            overflow = -tgt.hp
            tgt.hp = 0
            if tgt.is_pc:
                if overflow >= tgt.max_hp:
                    self.kill(tgt, src)
                    return amount
                tgt.dropped = True
                tgt.ds_succ = tgt.ds_fail = 0
                tgt.stable = False
                tgt.conditions["unconscious"] = {"rounds": 99, "save": None}
                tgt.raging = 0
                self.end_concentration(tgt)
                self.log(f"{tgt.name} drops to 0 HP")
            else:
                if tgt.traits.get("undead_fortitude") and dtype != "radiant" and not crit and \
                        self.make_save(tgt, "con", 5 + amount, important=False):
                    tgt.hp = 1
                    return amount
                self.kill(tgt, src)
                if src is not None and src.is_pc and src.features.get("dark_ones_blessing"):
                    src.temp_hp = max(src.temp_hp, src.features["dark_ones_blessing"])
        return amount

    def kill(self, c, src=None):
        c.dead = True
        c.hp = 0
        c.killed_by = src.name if src is not None else None
        self.end_concentration(c)
        self.log(f"{c.name} dies" if c.is_pc else f"{c.name} is defeated")

    def heal(self, tgt, amount, src=None):
        if tgt.dead or amount <= 0:
            return
        was_down = tgt.hp <= 0
        tgt.hp = min(tgt.max_hp, tgt.hp + amount)
        if was_down and tgt.hp > 0:
            tgt.conditions.pop("unconscious", None)
            tgt.ds_succ = tgt.ds_fail = 0
            tgt.stable = False
            self.log(f"{tgt.name} is healed back up ({amount})")

    def end_concentration(self, c):
        eff = c.concentration
        if not eff:
            return
        c.concentration = None
        self.remove_effect(eff)

    def remove_effect(self, eff):
        for tgt in eff.get("targets", []):
            tgt.buffs = [(b, e) for b, e in tgt.buffs if e is not eff]
            for name in [n for n, cd in tgt.conditions.items() if cd.get("effect") is eff]:
                tgt.conditions.pop(name, None)
        owner = eff["owner"]
        if eff in owner.effects:
            owner.effects.remove(eff)

    # -- expected values used for decisions --
    def p_hit(self, bonus, ac, adv=0):
        p = min(0.95, max(0.05, (21 - (ac - bonus)) / 20))
        if adv > 0:
            return 1 - (1 - p) ** 2
        if adv < 0:
            return p * p
        return p

    def p_fail(self, c, ability, dc):
        if ability in ("str", "dex") and AUTO_FAIL_STR_DEX & c.conditions.keys():
            return 1.0
        p = min(0.95, max(0.05, (dc - c.saves.get(ability, 0) - 1) / 20))
        if c.traits.get("magic_resistance"):
            p = p * p
        return p

    @staticmethod
    def rage_resists(t, dtype):
        if not t.raging:
            return False
        excl = t.features.get("rage_resist_all")
        return dtype in PHYSICAL or (excl is not None and dtype not in excl)

    def warded(self):
        return any(p.up and p.features.get("aura_warding") for p in self.party)

    def dmg_mult(self, tgt, dtype, magical=True):
        if dtype in tgt.immune or (not magical and dtype in tgt.immune_nm):
            return 0
        m = 1.0
        if dtype in tgt.resist or (not magical and dtype in tgt.resist_nm) or self.rage_resists(tgt, dtype):
            m = 0.5
        if dtype in tgt.vuln:
            m *= 2
        return m

    def threat(self, c):
        """Rough damage per round a creature deals; used to value control spells and targeting."""
        if not hasattr(c, "_threat"):
            best = 0
            for a in c.actions + c.spells if c.is_pc else c.actions:
                d = sum(avg(x["dice"]) for x in a.get("damage", [])) * max(1, a.get("rolls", 1))
                if a.get("weapon"):
                    d *= 1 + c.features.get("extra_attacks", 0)
                best = max(best, d * 0.65)
            for opt in c.multiattack:
                d = 0
                for nm, n in opt.items():
                    a = next((x for x in c.actions if x["name"].lower() == nm.lower()), None)
                    if a:
                        d += n * sum(avg(x["dice"]) for x in a.get("damage", [])) * 0.65
                best = max(best, d)
            c._threat = max(best, 1)
        return c._threat

    # -- targeting --
    def pick_target(self, c, cands):
        if not cands:
            return None
        pol = self.t.party_targeting if c.friendly else \
            self.t.per_monster.get(getattr(c, "index", ""), {}).get("targeting", self.t.monster_targeting)
        if pol == "focus_weakest":
            return min(cands, key=lambda x: (x.hp, x.ac))
        if pol == "focus_strongest":
            return max(cands, key=lambda x: self.threat(x))
        if pol == "focus_casters":
            casters = [x for x in cands if x.is_pc and x.cls in ("wizard", "sorcerer", "warlock", "bard", "cleric", "druid")]
            if casters:
                return self.rng.choice(casters)
        if pol == "nearest":
            return min(cands, key=lambda x: (self.dist(c, x), self.rng.random()))
        return self.rng.choice(cands)

    def targetable(self, c):
        foes = [f for f in self.foes(c) if f.up]
        if not c.friendly and self.t.finish_downed:
            downed = [f for f in self.foes(c) if f.present and f.hp <= 0 and not f.dead]
            foes = foes or downed
        return foes

    # -- resource accounting --
    def spend(self, c, res, n=1):
        if res.startswith("slot"):
            lv = int(res[4:])
            c.slots[lv] -= n
        else:
            c.resources[res] -= n
        c.res_used[res] = c.res_used.get(res, 0) + n

    def lam(self, c):
        """How reluctant a PC is to spend a unit of resource weight, given the danger."""
        base = {"nova": 0.0, "balanced": 5.0, "conserve": 14.0}.get(self.t.resource_policy, 5.0)
        foes_hp = sum(f.hp for f in self.foes(c) if f.up)
        party_dpr = sum(self.threat(p) for p in self.party if p.up) or 1
        rounds_needed = foes_hp / party_dpr
        lam = base / max(1.0, rounds_needed / 1.5)
        downs = sum(1 for p in self.party if p.present and p.hp <= 0)
        pcs = [p for p in self.party if p.is_pc] or self.party
        hp_frac = sum(max(p.hp, 0) for p in pcs) / sum(p.max_hp for p in pcs)
        if downs or hp_frac < 0.5:
            lam /= 2.5
        return lam

    # ------------------------------------------------------------------------------------------
    def run(self):
        s = self.s
        gap = max(5, s.start_rounds * 60)
        for p in self.party:
            p.x = 0
        for e in self.enemies:
            e.x = gap
        for c in self.all:
            adv = -1 if (self.ed == "2024" and ((s.surprise == "party" and c.friendly) or (s.surprise == "monsters" and not c.friendly))) else 0
            c.initiative = d20(self.rng, adv) + c.init + self.rng.random() / 10
        order = sorted(self.all, key=lambda c: -c.initiative)
        surprised = set()
        if self.ed == "2014" and s.surprise != "none":
            surprised = {id(c) for c in self.all if (c.friendly if s.surprise == "party" else not c.friendly)}
            for c in self.all:
                if id(c) in surprised:
                    c.reaction = False
        outcome = "stalemate"
        for self.round in range(1, s.max_rounds + 1):
            for c in order:
                if self.round == 1 and id(c) in surprised:
                    c.reaction = True  # surprise ends after the creature's (skipped) first turn
                    continue
                if c.dead or c.fled:
                    continue
                self.take_turn(c)
                self.legendary_window(c)
                o = self.outcome()
                if o:
                    outcome = o
                    break
            if outcome != "stalemate":
                break
        return outcome

    def outcome(self):
        if not any(e.up for e in self.enemies):
            return "victory"
        if not any(p.up for p in self.party):
            return "tpk"
        return None

    # ------------------------------------------------------------------------------------------
    def take_turn(self, c):
        c.reaction = True
        c.acted = True
        c.sneak_used = False
        c.sapped = False
        if c.legendary_max:
            c.legendary_points = c.legendary_max
            c.legendary_used = set()
        # Dying PCs roll death saves.
        if c.is_pc and c.hp <= 0:
            if c.stable or c.dead:
                return
            r = self.rng.randint(1, 20)
            if r == 20:
                self.heal(c, 1)
            elif r == 1:
                c.ds_fail += 2
            elif r >= 10:
                c.ds_succ += 1
            else:
                c.ds_fail += 1
            if c.ds_fail >= 3:
                self.kill(c)
            elif c.ds_succ >= 3:
                c.stable = True
            return
        if c.hp <= 0:
            return
        # Start-of-turn effects.
        regen = int(c.traits.get("regeneration", 0) or 0)
        if regen and c.hp > 0 and not getattr(c, "regen_blocked", False):
            c.hp = min(c.max_hp, c.hp + regen)
        c.regen_blocked = False
        for name, ready in list(c.recharge_ready.items()):
            if not ready:
                a = next((x for x in c.actions + c.bonus_actions if x["name"] == name), None)
                need = ((a or {}).get("uses") or {}).get("recharge_min", 6)
                if self.rng.randint(1, 6) >= need:
                    c.recharge_ready[name] = True
        for eff in list(c.effects):
            rec = eff.get("recurring")
            if rec and rec.get("trigger") == "auto":
                self.resolve_recurring(c, eff)
        aura = c.traits.get("damage_aura")
        if aura and c.up:
            for f in [f for f in self.foes(c) if f.up and self.dist(c, f) <= 10]:
                self.resolve_damage_ability(c, aura, [f])
        if c.raging:
            c.raging -= 1
        if c.starry:
            c.starry -= 1
        if c.twilight and c.up:
            c.twilight -= 1
            for x in self.allies(c):
                if x.up:
                    x.temp_hp = max(x.temp_hp, roll(self.rng, f"1d6+{c.level}"))
        if not c.up:
            return
        # Non-PC creatures can flee; allies only when the Tactician says so (e.g. horses that bolt).
        default_flee = 0 if c.friendly else self.t.flee_at
        flee = self.t.per_monster.get(getattr(c, "index", ""), {}).get("flee_at", default_flee) if not c.is_pc else 0
        if flee > 0 and (flee >= 1 or c.hp < c.max_hp * flee):
            c.fled = True
            self.log(f"{c.name} flees")
            return
        if c.can_act():
            c.conditions.pop("prone", None)
            if c.is_pc:
                self.pc_turn(c)
            else:
                self.monster_turn(c)
        self.end_of_turn(c)

    def end_of_turn(self, c):
        for name, cd in list(c.conditions.items()):
            if name == "unconscious" and c.hp <= 0:
                continue
            cd["rounds"] -= 1
            if cd.get("save") and cd.get("save_ends", True) and c.up:
                if self.make_save(c, cd["save"], cd["dc"], is_spell=cd.get("spell", False), important=True):
                    cd["rounds"] = 0
            if cd["rounds"] <= 0:
                c.conditions.pop(name, None)
                eff = cd.get("effect")
                if eff and not any(cd2.get("effect") is eff for t in eff.get("targets", []) for cd2 in t.conditions.values()):
                    if eff.get("kind") == "control" and eff["owner"].concentration is eff:
                        eff["owner"].concentration = None
                        self.remove_effect(eff)
        for eff in list(c.effects):
            eff["rounds"] -= 1
            if eff["rounds"] <= 0:
                if c.concentration is eff:
                    c.concentration = None
                self.remove_effect(eff)
        if c.vex_target is not None and not getattr(c, "_vex_fresh", False):
            c.vex_target = None
        c._vex_fresh = False

    def legendary_window(self, acted):
        for m in self.all:
            if m.is_pc or m is acted or not m.legendary_max or m.legendary_points <= 0 or not m.can_act():
                continue
            best, best_v = None, 0
            used = getattr(m, "legendary_used", set())
            for i, la in enumerate(m.legendary):
                cost = la.get("cost", 1) or 1
                if cost > m.legendary_points or (la.get("once_per_round") and i in used):
                    continue
                a = la.get("ability")
                if la.get("uses_action"):
                    a = next((x for x in m.actions if x["name"].lower() == la["uses_action"].lower()), a)
                if not a or a.get("kind") in ("utility", "summon"):
                    continue
                plan = self.plan_for(m, a)
                if plan and plan[0] / cost > best_v:
                    best, best_v = (plan, cost, i), plan[0] / cost
            if best:
                (v, a, targets, extra), cost, i = best
                m.legendary_points -= cost
                used.add(i)
                m.legendary_used = used
                self.execute(m, a, targets, extra)

    # ------------------------------------------------------------------------------------------
    # Planning: every option becomes (value, ability, targets, extra) using expected damage.
    # ------------------------------------------------------------------------------------------
    def reachable(self, c, a, cands):
        only = TYPE_ONLY.get(a["name"].lower())
        if only:
            cands = [f for f in cands if only in f.ctype]
        rng = a.get("range", 5) or 5
        if a.get("melee") or rng <= 10:
            reach = max(5, rng if a.get("melee") else 5)
            return [f for f in cands if self.dist(c, f) - reach <= c.speed]
        return [f for f in cands if self.dist(c, f) <= rng + c.speed]

    def ev_single(self, c, a, tgt, slot_bonus=0):
        """Expected value of an ability against one target (damage-equivalent units)."""
        kind = a.get("kind")
        dmg = sum(avg(d["dice"]) * self.dmg_mult(tgt, d.get("type", "force"), magical=a.get("is_spell") or c.magic_weapons or not c.is_pc)
                  * (0.3 if d.get("adv_only") else 1) for d in a.get("damage", []))
        dmg += slot_bonus
        dmg += self.rider_avg(c, a, tgt)
        rolls = max(1, a.get("rolls", 1) or 1)
        v = 0
        if kind == "attack":
            ph = self.p_hit(self.attack_bonus(c, a), self.ac_of(tgt), self.attack_adv(c, tgt, a.get("melee", False)))
            v = ph * dmg * rolls
            if a.get("condition"):
                v += ph * self.condition_value(c, a, tgt)
        elif kind == "auto":
            v = dmg * rolls
        elif kind in ("save", "control", "debuff"):
            sv = a.get("save") or {}
            pf = self.p_fail(tgt, sv.get("ability", "dex"), sv.get("dc") or c.spell_dc)
            v = pf * dmg + (1 - pf) * (dmg / 2 if sv.get("half") else 0)
            if a.get("condition"):
                v += pf * self.condition_value(c, a, tgt)
            if a.get("buff") and kind == "debuff":
                v += pf * 0.1 * self.threat(tgt) * min(a.get("duration", 1), 3)
        rec = a.get("recurring")
        if rec:
            rd = sum(avg(d["dice"]) for d in rec.get("damage", []))
            v += 0.6 * rd * max(1, rec.get("targets", 1)) * min(a.get("duration", 1), 3)
        return v

    def condition_value(self, c, a, tgt):
        cd = a["condition"]
        name = cd["name"]
        if name in tgt.cond_immune or name in tgt.conditions:
            return 0
        if name == "frightened" and tgt.friendly and any(p.up and p.features.get("aura_resist_fear") for p in self.party):
            return 0
        weight = {"paralyzed": 2.2, "stunned": 2.0, "banished": 1.8, "petrified": 2.0, "incapacitated": 1.4,
                  "unconscious": 2.0, "charmed": 0.9, "restrained": 0.8, "blinded": 0.7, "frightened": 0.5,
                  "poisoned": 0.4, "prone": 0.25, "outlined": 0.35, "slowed": 0.7, "grappled": 0.1}.get(name, 0.3)
        dur = min(cd.get("duration", 1), 3) if not cd.get("save_ends", True) else 1.8
        if cd.get("breaks_on_damage"):
            dur = min(dur, 1.2)
        lr = 0.2 if tgt.legendary_resistance > 0 else 1.0  # will just burn a Legendary Resistance
        return weight * self.threat(tgt) * dur * lr

    def attack_bonus(self, c, a):
        b = a.get("attack_bonus") or 0
        b += c.attack_bonus_items if (a.get("weapon") or a.get("is_spell")) else 0
        if c.is_pc and a.get("weapon") and getattr(c, "sacred", 0):
            b += c.sacred
        for buff, _ in c.buffs:
            if buff.get("attack_bonus"):
                b += avg(buff["attack_bonus"])
            if buff.get("attack_penalty"):
                b -= avg(buff["attack_penalty"])
        return b

    def rider_avg(self, c, a, tgt):
        """Average extra damage from features on a weapon/spell hit (for planning)."""
        if not c.is_pc:
            return 0
        x = 0
        f = c.features
        if a.get("weapon"):
            if c.raging and a.get("melee"):
                x += f.get("rage_damage", 0)
            if f.get("improved_smite") and a.get("melee"):
                x += 4.5
            for b, _ in c.buffs:
                if b.get("damage_bonus"):
                    x += avg(b["damage_bonus"])
        if a.get("is_spell") and a.get("kind") == "attack":
            for b, _ in c.buffs:
                if b.get("damage_bonus"):
                    x += avg(b["damage_bonus"])
        for e in c.dmg_bonus_items:
            if (a.get("weapon") or e.get("spells_too")) and (not e.get("vs_creature_type") or e["vs_creature_type"] in tgt.ctype):
                x += avg(e["dice"])
        return x

    def area_targets(self, c, a, primary, cands):
        n = data.targets_in_area(a.get("area"), len(cands)) if a.get("area") else max(1, a.get("targets", 1) or 1)
        others = sorted([f for f in cands if f is not primary], key=lambda f: self.dist(primary, f))
        return [primary] + others[: n - 1]

    def plan_for(self, c, a, slot_level=None):
        """Best (value, ability, targets, extra) for using ability a now, or None."""
        kind = a.get("kind")
        if kind in ("utility", "summon"):
            return None
        u = a.get("uses") or {}
        if u.get("type") == "recharge" and not c.recharge_ready.get(a["name"], True):
            return None
        if a["name"] in c.uses and c.uses[a["name"]] <= 0:
            return None
        if (a.get("recurring") or a.get("concentration")) and any(e.get("name") == a["name"] for e in c.effects):
            return None
        extra = {"slot": slot_level}
        if kind == "heal":
            return self.plan_heal(c, a, slot_level)
        if kind == "buff":
            return self.plan_buff(c, a, slot_level)
        cands = self.reachable(c, a, self.targetable(c))
        if not cands:
            return None
        a2 = a
        if slot_level and a.get("spell_level") is not None and slot_level > a["spell_level"]:
            a2 = self.upcast(a, slot_level - a["spell_level"])
        primary = self.pick_target(c, cands)
        if a2.get("area") or (a2.get("targets", 1) or 1) > 1:
            if a2.get("kind") == "attack" and (a2.get("rolls", 1) or 1) > 1:
                targets = [primary]
            else:
                targets = self.area_targets(c, a2, primary, cands)
        else:
            targets = [primary]
        v = sum(self.ev_single(c, a2, t) for t in targets)
        if a2.get("concentration") and c.concentration:
            v -= self.conc_value(c.concentration)
        return (v, a2, targets, extra)

    def conc_value(self, eff):
        return 0.5 * eff.get("value", 5) * max(0, eff.get("rounds", 1)) / 3

    def upcast(self, a, k):
        a = copy.deepcopy(a)
        up = a.get("upcast") or {}
        if up.get("dice") and a.get("damage"):
            a["damage"].append({"dice": scale(up["dice"], k), "type": a["damage"][0].get("type", "force")})
        if up.get("dice") and a.get("heal"):
            a["heal"] = f"{a['heal']}+{scale(up['dice'], k)}"
        a["targets"] = (a.get("targets", 1) or 1) + k * (up.get("extra_targets", 0) or 0)
        a["rolls"] = (a.get("rolls", 1) or 1) + k * (up.get("extra_rolls", 0) or 0)
        return a

    def plan_heal(self, c, a, slot_level):
        allies = [x for x in self.allies(c) if not x.dead and x.hp < x.max_hp]
        rng = a.get("range", 5) or 5
        allies = [x for x in allies if self.dist(c, x) <= max(rng, 5) + c.speed]
        if not allies:
            return None
        a2 = self.upcast(a, slot_level - a["spell_level"]) if slot_level and a.get("spell_level") and slot_level > a["spell_level"] else a
        amt = avg(a2.get("heal") or "0") + (c.features.get("spell_mod", 0) if a.get("is_spell") else 0)
        if c.features.get("disciple_of_life") and a.get("is_spell"):
            amt += 2 + (slot_level or a.get("spell_level", 1))
        n = max(1, a2.get("targets", 1) or 1)
        allies.sort(key=lambda x: (x.hp > 0, x.hp / x.max_hp))
        chosen = allies[:n]
        v = 0
        for x in chosen:
            if x.hp <= 0:
                v += 25 + min(amt, x.max_hp) * 0.5
            elif x.hp < 0.35 * x.max_hp:
                v += min(amt, x.max_hp - x.hp) * 0.5
            else:
                v += min(amt, x.max_hp - x.hp) * 0.05
        return (v, a2, chosen, {"slot": slot_level, "heal": amt})

    def plan_buff(self, c, a, slot_level):
        b = a.get("buff") or {}
        if a.get("cost") == "reaction":
            return None
        if any(e.get("name") == a["name"] for e in c.effects):
            return None
        allies = [x for x in self.allies(c) if x.up]
        if not allies or not self.foes(c):
            return None
        n = max(1, a.get("targets", 1) or 1)
        if slot_level and a.get("spell_level") and slot_level > a["spell_level"]:
            n += (slot_level - a["spell_level"]) * ((a.get("upcast") or {}).get("extra_targets", 0) or 0)
        rounds = min(a.get("duration", 1) or 1, 4)
        foes_dpr = sum(self.threat(f) for f in self.foes(c) if f.up)
        if b.get("damage_bonus"):  # Hex / Hunter's Mark / Divine Favor: rides on own attacks
            chosen = [c]
            per_round = avg(b["damage_bonus"]) * 0.65 * (1 + c.features.get("extra_attacks", 0) if c.features.get("extra_attacks") else 1)
            if c.cls == "warlock":
                per_round = avg(b["damage_bonus"]) * 0.65 * c.tier
            v = per_round * rounds
        else:
            chosen = sorted(allies, key=lambda x: -self.threat(x))[:n]
            v = 0
            for x in chosen:
                if b.get("attack_bonus"):
                    v += avg(b["attack_bonus"]) * 0.05 * self.threat(x) * rounds / 0.65
                if b.get("ac"):
                    v += b["ac"] * 0.05 * foes_dpr / max(1, len(allies)) * rounds
                if b.get("extra_attack"):
                    v += 0.5 * self.threat(x) * rounds
                if b.get("advantage_attacks"):
                    v += 0.25 * self.threat(x) * rounds
                if b.get("temp_hp") or a.get("temp_hp"):
                    v += avg(a.get("temp_hp") or "5") * 0.3
            if a.get("temp_hp"):
                v += avg(a["temp_hp"]) * 0.3 * len(chosen)
        if a.get("concentration") and c.concentration:
            v -= self.conc_value(c.concentration)
        return (v, a, chosen, {"slot": slot_level})

    # ------------------------------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------------------------------
    def close_distance(self, c, a, targets):
        if not targets:
            return
        t = targets[0]
        if a.get("melee") or (a.get("range", 5) or 5) <= 10:
            reach = max(5, a.get("range", 5) if a.get("melee") else 5)
            self.move_toward(c, t, c.speed, reach)
        elif self.dist(c, t) > (a.get("range", 5) or 5):
            self.move_toward(c, t, c.speed, a.get("range", 5))

    def execute(self, c, a, targets, extra):
        slot = (extra or {}).get("slot")
        self.close_distance(c, a, targets)
        if a.get("melee") or (a.get("range", 5) or 5) <= 10:
            reach = max(5, a.get("range", 5) if a.get("melee") else 5)
            targets = [t for t in targets if self.dist(c, t) <= reach + 0.1] or []
            if not targets:
                return
        u = a.get("uses") or {}
        if u.get("type") == "recharge":
            c.recharge_ready[a["name"]] = False
        if a["name"] in c.uses:
            c.uses[a["name"]] -= 1
        if a.get("item_uses") is not None:
            self.spend(c, "item_charge")
        kind = a.get("kind")
        self.log(f"{c.name} uses {a['name']}" + (f" (level {slot} slot)" if slot else "") +
                 (" on " + ", ".join(t.name for t in targets) if targets else ""))
        if kind == "heal":
            amt_expr = a.get("heal") or "0"
            for t in targets:
                amt = roll(self.rng, amt_expr) + (c.features.get("spell_mod", 0) if a.get("is_spell") else 0)
                if c.is_pc and c.features.get("disciple_of_life") and a.get("is_spell"):
                    amt += 2 + (slot or a.get("spell_level", 1))
                self.heal(t, amt, c)
            if a.get("is_spell") and a.get("concentration"):
                pass
            return
        if kind == "buff":
            self.start_effect(c, a, targets, kind="buff")
            return
        effect = None
        if a.get("concentration") or a.get("recurring") or (a.get("condition") and a.get("duration", 1) > 1):
            effect = self.start_effect(c, a, targets, kind="control")
        if kind == "debuff" and a.get("buff"):
            for t in targets:
                sv = a.get("save") or {}
                if not sv or not self.make_save(t, sv.get("ability", "cha"), sv.get("dc") or c.spell_dc, is_spell=True):
                    t.buffs.append((a["buff"], effect))
            if a.get("damage"):
                self.resolve_damage_ability(c, a, targets, effect)
            return
        self.resolve_damage_ability(c, a, targets, effect)

    def start_effect(self, c, a, targets, kind):
        if a.get("concentration"):
            self.end_concentration(c)
        eff = {"name": a["name"], "owner": c, "targets": list(targets), "rounds": max(1, a.get("duration", 1) or 1),
               "recurring": a.get("recurring"), "kind": kind, "ability": a, "value": 5}
        if kind == "buff":
            b = a.get("buff") or {}
            for t in targets:
                if b:
                    t.buffs.append((b, eff))
                if a.get("temp_hp"):
                    t.temp_hp = max(t.temp_hp, roll(self.rng, a["temp_hp"]))
            eff["value"] = 8
        if a.get("concentration"):
            c.concentration = eff
        c.effects.append(eff)
        return eff

    def resolve_recurring(self, c, eff):
        rec = eff["recurring"]
        a = {"name": eff["name"] + " (ongoing)", "kind": "attack" if rec.get("attack") else ("save" if rec.get("save") else "auto"),
             "damage": rec.get("damage", []), "save": rec.get("save"), "attack_bonus": c.spell_attack,
             "range": 60, "targets": rec.get("targets", 1), "is_spell": True}
        cands = [f for f in self.foes(c) if f.up]
        if not cands:
            return
        primary = self.pick_target(c, cands)
        targets = self.area_targets(c, dict(a, area=None), primary, cands)[: max(1, rec.get("targets", 1))]
        self.log(f"{c.name}'s {eff['name']} strikes " + ", ".join(t.name for t in targets))
        self.resolve_damage_ability(c, a, targets, eff, recurring=True)

    def resolve_damage_ability(self, c, a, targets, effect=None, recurring=False):
        kind = a.get("kind")
        magical = bool(a.get("is_spell") or c.magic_weapons or not c.is_pc)
        rolls = max(1, a.get("rolls", 1) or 1)
        if kind == "attack":
            for i in range(rolls):
                live = [t for t in targets if t.present and (t.hp > 0 or self.t.finish_downed or c.is_pc is False)]
                if not live:
                    break
                t = live[i % len(live)] if len(live) > 1 else live[0]
                if not t.present:
                    continue
                self.attack_roll(c, a, t, magical, effect)
        elif kind == "auto":
            for i in range(rolls):
                live = [t for t in targets if t.up]
                if not live:
                    break
                t = live[i % len(live)]
                for d in a.get("damage", []):
                    self.apply_damage(t, roll(self.rng, d["dice"]), d.get("type", "force"), c, magical=magical,
                                      spell=bool(a.get("is_spell")))
        else:  # save-based (save / control / debuff with damage)
            sv = a.get("save") or {"ability": "dex", "dc": c.spell_dc, "half": False}
            dc = sv.get("dc") or c.spell_dc
            dmg_rolls = [(roll(self.rng, d["dice"]), d.get("type", "force")) for d in a.get("damage", [])]
            bonus_once = self.spell_bonus(c, a)
            for t in targets:
                if not t.present:
                    continue
                ok = self.make_save(t, sv.get("ability", "dex"), dc, is_spell=bool(a.get("is_spell")),
                                    important=bool(a.get("condition")) or sum(x for x, _ in dmg_rolls) > t.hp * 0.25)
                if ok and c.resources.get("portent", 0) > 0 and a.get("is_spell") and not t.legendary_resistance \
                        and (a.get("condition") or sum(x for x, _ in dmg_rolls) > t.hp * 0.4) and self.threat(t) >= 8:
                    self.spend(c, "portent")
                    ok = False
                    self.log(f"{c.name} uses Portent on {t.name}")
                evasion = t.is_pc and t.features.get("evasion") and sv.get("ability") == "dex" and sv.get("half")
                for i, (amt, dt) in enumerate(dmg_rolls):
                    amt += bonus_once if i == 0 else 0
                    if ok:
                        amt = 0 if (evasion or not sv.get("half")) else amt // 2
                    elif evasion:
                        amt //= 2
                    self.apply_damage(t, amt, dt, c, magical=magical, spell=bool(a.get("is_spell")))
                if not ok and a.get("condition") and t.present:
                    self.apply_condition(c, a, t, effect, sv)
                elif ok and a.get("condition"):
                    self.log(f"{t.name} resists {a['name']}")

    def spell_bonus(self, c, a):
        if not c.is_pc or not a.get("is_spell") or not a.get("damage"):
            return 0
        b = 0
        if c.features.get("empowered_evocation") and a.get("school", "").lower() == "evocation":
            b += c.features["empowered_evocation"]
        if c.features.get("elemental_affinity") and a["damage"][0].get("type") == "fire":
            b += c.features["elemental_affinity"]
        if c.features.get("radiant_soul") and a["damage"][0].get("type") in ("fire", "radiant"):
            b += c.features["radiant_soul"]
        return b

    def apply_condition(self, c, a, t, effect, sv):
        cd = a["condition"]
        name = cd["name"]
        if name in t.cond_immune:
            return
        if name == "frightened" and t.friendly and any(p.up and p.features.get("aura_resist_fear") for p in self.party):
            return
        if name == "banished" and t.cr is not None and False:
            return
        t.conditions[name] = {"rounds": max(1, cd.get("duration", 1) or 1), "save": cd.get("save_ability") or sv.get("ability"),
                              "dc": sv.get("dc") or c.spell_dc, "save_ends": cd.get("save_ends", True),
                              "breaks": cd.get("breaks_on_damage", False), "effect": effect, "spell": bool(a.get("is_spell"))}
        if name in ("paralyzed", "stunned", "unconscious", "petrified", "incapacitated", "banished"):
            self.end_concentration(t)
        self.log(f"{t.name} is {name}")

    def attack_roll(self, c, a, t, magical, effect=None):
        melee = bool(a.get("melee"))
        adv = self.attack_adv(c, t, melee)
        flare = self.flare_for(c, t) if not c.friendly else None
        if flare:
            flare.reaction = False
            self.spend(flare, "warding_flare")
            adv = max(-1, adv - 1)
            self.log(f"{flare.name} uses Warding Flare against {c.name}")
        r = d20(self.rng, adv)
        bonus = (a.get("attack_bonus") or 0) + (c.attack_bonus_items if (a.get("weapon") or a.get("is_spell")) else 0)
        if c.is_pc and a.get("weapon") and getattr(c, "sacred", 0):
            bonus += c.sacred
        for buff, _ in c.buffs:
            if buff.get("attack_bonus"):
                bonus += roll(self.rng, buff["attack_bonus"])
            if buff.get("attack_penalty"):
                bonus -= roll(self.rng, buff["attack_penalty"])
        crit_range = c.features.get("crit", 20) if c.is_pc else 20
        if c.cursed is t:
            crit_range = min(crit_range, 19)
        crit = r >= crit_range
        total = r + bonus
        ac = self.ac_of(t)
        hit = r != 1 and (crit or total >= ac)
        # Reactions that can turn a hit into a miss.
        if hit and not crit and t.reaction:
            if t.is_pc:
                shield = next((s for s in t.spells if s.get("cost") == "reaction" and (s.get("buff") or {}).get("ac")), None)
                if shield and total < ac + 5 and self.lowest_slot(t, 1):
                    lv = self.lowest_slot(t, 1)
                    self.spend(t, f"slot{lv}")
                    t.reaction = False
                    t.buffs.append(({"ac": 5}, {"owner": t, "targets": [t], "rounds": 1}))
                    t.effects.append({"name": "Shield", "owner": t, "targets": [t], "rounds": 1, "kind": "buff"})
                    hit = False
                    self.log(f"{t.name} casts Shield")
                elif t.features.get("cutting_words") and t.resources.get("bardic_inspiration", 0) > 0 and total - ac < 4:
                    pass
            elif t.traits.get("reaction_ac") and melee and total < ac + t.traits["reaction_ac"]:
                t.reaction = False
                hit = False
        if (melee and self.dist(c, t) <= 5) and ({"paralyzed", "unconscious"} & t.conditions.keys()) and hit:
            crit = True
        if hit and c.features.get("assassinate") and self.ed == "2014" and self.round == 1 and self.s.surprise == "monsters":
            crit = True
        if not hit:
            if c.is_pc and c.features.get("graze") and a.get("weapon") and melee:
                self.apply_damage(t, max(0, c.mods.get("str", 0)), a["damage"][0].get("type", "slashing"), c, magical=magical)
            return False
        # Damage on a hit.
        parts = [(roll(self.rng, d["dice"], crit), d.get("type", "force")) for d in a.get("damage", [])
                 if not d.get("adv_only") or adv > 0]
        extra = self.on_hit_riders(c, a, t, crit)
        parts.extend(extra)
        sb = self.spell_bonus(c, a)
        if sb and parts:
            parts[0] = (parts[0][0] + sb, parts[0][1])
        if c.is_pc and c.features.get("brutal_critical") and crit and a.get("weapon") and melee:
            parts.append((roll(self.rng, f"{c.features['brutal_critical']}d12"), parts[0][1]))
        total_dmg = sum(x for x, _ in parts)
        if t.is_pc and t.reaction and t.features.get("uncanny_dodge") and total_dmg >= 8 and t.hp > 0:
            t.reaction = False
            parts = [(x // 2, dt) for x, dt in parts]
        if t.is_pc and t.reaction and t.features.get("deflect") and not melee and not a.get("is_spell") and t.hp > 0:
            t.reaction = False
            red = roll(self.rng, "1d10") + t.mods.get("dex", 0) + t.level
            parts = [(max(0, x - red), dt) for x, dt in parts]
        for amt, dt in parts:
            self.apply_damage(t, amt, dt, c, crit=crit, magical=magical, melee=melee, spell=bool(a.get("is_spell")))
        if c.is_pc and c.features.get("vex") and a.get("weapon"):
            c.vex_target, c._vex_fresh = t, True
        if c.is_pc and c.features.get("sap") and a.get("weapon"):
            t.sapped = True
        if a.get("condition") and t.present and t.hp > 0:
            sv = a.get("save")
            if sv:
                if not self.make_save(t, sv.get("ability", "con"), sv.get("dc") or c.spell_dc, is_spell=bool(a.get("is_spell"))):
                    self.apply_condition(c, a, t, effect, sv)
            else:
                self.apply_condition(c, a, t, effect, {"ability": a["condition"].get("save_ability") or "str", "dc": 10 + (a.get("attack_bonus") or 0) - 2})
        # Monk Stunning Strike.
        if c.is_pc and c.features.get("stunning_strike") and melee and t.up and c.resources.get("ki", 0) > 0 \
                and "stunned" not in t.conditions and "stunned" not in t.cond_immune and not getattr(c, "_stun_tried", False) \
                and self.threat(t) >= 8 and self.lam(c) < 6:
            self.spend(c, "ki")
            c._stun_tried = self.ed == "2024"
            if not self.make_save(t, "con", c.features["ki_dc"], important=True):
                t.conditions["stunned"] = {"rounds": 1, "save": None, "dc": 0, "save_ends": False, "effect": None}
                self.end_concentration(t)
                self.log(f"{c.name} stuns {t.name}")
        if "regen_blocked_by" in t.traits and any(dt in (t.traits.get("regen_blocked_by") or []) for _, dt in parts):
            t.regen_blocked = True
        return True

    def flare_for(self, att, tgt):
        """A Light cleric who can spend Warding Flare against this attack, if any."""
        if self.threat(att) < 6:
            return None
        for p in self.party:
            if p.up and p.reaction and p.resources.get("warding_flare", 0) > 0 and \
                    (p is tgt or (self.ed == "2024" and tgt.friendly)) and "blinded" not in att.cond_immune:
                return p
        return None

    def on_hit_riders(self, c, a, t, crit):
        out = []
        if not c.is_pc:
            sa = c.traits.get("sneak_attack")
            if sa and not c.sneak_used and a.get("kind") == "attack":
                c.sneak_used = True
                out.append((roll(self.rng, sa, crit), "piercing"))
            return out
        f = c.features
        weapon = a.get("weapon")
        melee = a.get("melee")
        dtype = (a.get("damage") or [{"type": "force"}])[0].get("type", "force")
        if weapon:
            if c.raging and melee:
                out.append((f.get("rage_damage", 2), dtype))
                if f.get("frenzy") and self.ed == "2024" and not c.sneak_used:
                    c.sneak_used = True
                    out.append((roll(self.rng, f"{f.get('rage_damage', 2)}d6", crit), dtype))
            if f.get("divine_fury") and c.raging and not c._df_used:
                c._df_used = True
                out.append((roll(self.rng, f"1d6+{c.level // 2}", crit), "radiant"))
            if f.get("blade_flourish") and not c._bf_used and c.resources.get("bardic_inspiration", 0) > 0 and self.lam(c) < 3:
                c._bf_used = True
                self.spend(c, "bardic_inspiration")
                out.append((roll(self.rng, f.get("inspiration_die", "1d6"), crit), dtype))
            if f.get("sup_die") and not c._bm_used and c.resources.get("superiority", 0) > 0 and (crit or self.lam(c) < 3):
                c._bm_used = True
                self.spend(c, "superiority")
                out.append((roll(self.rng, f["sup_die"], crit), dtype))
                if "prone" not in t.conditions and t.size in ("Tiny", "Small", "Medium", "Large") and "prone" not in t.cond_immune \
                        and not self.make_save(t, "str", f["maneuver_dc"], important=False):
                    t.conditions["prone"] = {"rounds": 1, "save": None, "dc": 0, "save_ends": False, "effect": None}
                    self.log(f"{c.name} trips {t.name}")
            if f.get("hand_of_harm") and not c._hoh_used and c.resources.get("ki", 0) > 0 and self.lam(c) < 4 \
                    and a["name"] in ("Unarmed Strike", "Martial Arts", "Flurry of Blows"):
                c._hoh_used = True
                self.spend(c, "ki")
                out.append((roll(self.rng, f"{f.get('martial_arts_die', '1d6')}+{max(1, c.mods['wis'])}", crit), "necrotic"))
            if f.get("dread_ambusher"):
                if self.ed == "2014" and c._da_bonus:
                    out.append((roll(self.rng, "1d8", crit), dtype))
                elif self.ed == "2024" and not c._da2_used and c.resources.get("dread_ambusher", 0) > 0 and \
                        (self.round <= 1 or self.lam(c) < 2):
                    c._da2_used = True
                    self.spend(c, "dread_ambusher")
                    out.append((roll(self.rng, "2d6", crit), "psychic"))
            if f.get("dreadful_strikes") and not c._fw_used:
                c._fw_used = True
                out.append((roll(self.rng, f["dreadful_strikes"], crit), "psychic"))
            if f.get("hex_curse_bonus") and c.cursed is t:
                out.append((f["hex_curse_bonus"], dtype))
            if f.get("assassinate") and self.ed == "2024" and self.round == 1 and not c._as_used:
                c._as_used = True
                out.append((c.level, dtype))
            if f.get("sneak_attack") and not c.sneak_used and (
                    self.attack_adv(c, t, melee) > 0 or any(x is not c and x.up and self.dist(x, t) <= 5 for x in self.allies(c))
                    or (f.get("rakish") and melee and self.dist(c, t) <= 5)):
                c.sneak_used = True
                out.append((roll(self.rng, f["sneak_attack"], crit), dtype))
            if f.get("colossus_slayer") and not getattr(c, "_cs_used", False) and t.hp < t.max_hp:
                c._cs_used = True
                out.append((roll(self.rng, "1d8", crit), dtype))
            if f.get("improved_smite") and melee:
                out.append((roll(self.rng, "1d8", crit), "radiant"))
            if f.get("divine_strike") and not getattr(c, "_ds_used", False):
                c._ds_used = True
                out.append((roll(self.rng, f["divine_strike"], crit), "radiant"))
            # Divine Smite: spend a slot on a crit, or on a big target when resources are worth spending.
            if f.get("smite") and melee and t.up and not (self.ed == "2024" and getattr(c, "_smite_used", False)):
                lv = self.lowest_slot(c, 1)
                if lv and (crit or (self.lam(c) * lv < 4.5 * (2 + lv) and t.hp > 10)):
                    self.spend(c, f"slot{lv}")
                    c._smite_used = True
                    dice = min(5, 1 + lv) + (1 if t.ctype in ("undead", "fiend") else 0)
                    out.append((roll(self.rng, f"{dice}d8", crit), "radiant"))
                    self.log(f"{c.name} smites ({lv})")
        for b, _ in c.buffs:
            if b.get("damage_bonus") and (weapon or a.get("is_spell")):
                out.append((roll(self.rng, b["damage_bonus"], crit), "necrotic" if c.cls == "warlock" else dtype))
        for e in c.dmg_bonus_items:
            if (weapon or e.get("spells_too")) and (not e.get("vs_creature_type") or e["vs_creature_type"] in t.ctype):
                out.append((roll(self.rng, e["dice"], crit), e.get("damage_type") or dtype))
        return out

    def lowest_slot(self, c, min_level):
        for lv in sorted(c.slots):
            if lv >= min_level and c.slots[lv] > 0:
                return lv
        return None

    # ------------------------------------------------------------------------------------------
    def monster_turn(self, c):
        options = []
        if c.multiattack:
            for opt in c.multiattack:
                v, parts = 0, []
                for nm, n in opt.items():
                    a = next((x for x in c.actions if x["name"].lower() == nm.lower()), None)
                    if not a or a.get("kind") in ("utility", "summon"):
                        continue
                    p = self.plan_for(c, a)
                    if p:
                        v += p[0] * n
                        parts.append((a, n))
                if parts:
                    options.append((v, "multi", parts))
        for a in c.actions:
            slot = None
            if a.get("mon_spell_uses") == "slot":
                slot = self.lowest_slot(c, a.get("spell_level", 1))
                if not slot:
                    continue
            p = self.plan_for(c, a, slot)
            if p:
                options.append((p[0], "single", p))
        moved = False
        if options:
            options.sort(key=lambda o: -o[0])
            v, typ, payload = options[0]
            if v <= 0.01:
                options = []
            elif typ == "multi":
                for a, n in payload:
                    for _ in range(n):
                        p = self.plan_for(c, a)
                        if p:
                            self.execute(c, p[1], p[2], p[3])
                            moved = True
            else:
                v, a, targets, extra = payload
                if a.get("mon_spell_uses") == "slot" and extra.get("slot"):
                    self.spend(c, f"slot{extra['slot']}")
                self.execute(c, a, targets, extra)
                moved = True
        if not moved:
            self.dash(c)
        # Bonus actions.
        best = None
        for a in c.bonus_actions:
            p = self.plan_for(c, a)
            if p and (best is None or p[0] > best[0]):
                best = p
        if best and best[0] > 0.5:
            self.execute(c, best[1], best[2], best[3])

    def dash(self, c):
        foes = [f for f in self.foes(c) if f.up] or self.foes(c)
        if not foes:
            return
        t = min(foes, key=lambda f: self.dist(c, f))
        self.move_toward(c, t, c.speed * 2)
        self.log(f"{c.name} dashes toward {t.name}")

    # ------------------------------------------------------------------------------------------
    def pc_options(self, c, cost):
        """All (adjusted value, raw value, plan, resource-key) options for an action or bonus action."""
        opts = []
        lam = self.lam(c)
        if cost == "action" and c.features.get("radiance_of_dawn") and c.resources.get("channel_divinity", 0) > 0:
            a = {"name": "Radiance of the Dawn", "kind": "save", "range": 30, "area": {"shape": "emanation", "size": 30},
                 "save": {"ability": "con", "dc": c.spell_dc, "half": True},
                 "damage": [{"dice": f"2d10+{c.level}", "type": "radiant"}], "targets": 1}
            p = self.plan_for(c, a)
            if p:
                opts.append((p[0] - lam * RESOURCE_WEIGHTS["channel_divinity"], p[0], ("spell", p), "channel_divinity"))
        if cost == "action":
            for a in c.actions:  # weapon attacks (Attack action with Extra Attack)
                p = self.plan_for(c, a)
                if p:
                    n = 1 + c.features.get("extra_attacks", 0)
                    opts.append((p[0] * n, p[0] * n, ("attack", a, n), None))
        for a in c.spells + c.item_spells:
            if a.get("cost", "action") != cost:
                continue
            if cost == "action" and getattr(c, "_bonus_spell", False) and a.get("spell_level", 0) > 0:
                continue
            if a.get("item_uses") is not None:
                if c.resources.get("item_charge", 0) <= 0:
                    continue
                p = self.plan_for(c, a)
                if p:
                    opts.append((p[0] - lam * RESOURCE_WEIGHTS["item_charge"], p[0], ("spell", p), "item_charge"))
                continue
            lvl = a.get("spell_level", 0)
            if lvl == 0:
                p = self.plan_for(c, a)
                if p:
                    opts.append((p[0], p[0], ("spell", p), None))
                continue
            if a["name"] == "Hunter's Mark" and c.resources.get("hunters_mark_free", 0) > 0:
                p = self.plan_for(c, a, lvl)
                if p:
                    opts.append((p[0] - lam * 1.0, p[0], ("spell", p), "hunters_mark_free"))
                continue
            levels = sorted({lv for lv in c.slots if lv >= lvl and c.slots[lv] > 0})
            for lv in levels[:1] + levels[-1:] if levels else []:
                p = self.plan_for(c, a, lv)
                if p:
                    opts.append((p[0] - lam * lv, p[0], ("spell", p), f"slot{lv}"))
        return opts

    def do_option(self, c, opt):
        _, _, plan, res = opt
        if plan[0] == "attack":
            _, a, n = plan
            for _ in range(n):
                p = self.plan_for(c, a)
                if not p:
                    break
                self.execute(c, p[1], p[2], p[3])
            if c.resources.get("ammunition") is not None and not a.get("melee"):
                if c.resources["ammunition"] > 0:
                    self.spend(c, "ammunition", min(n, c.resources["ammunition"]))
            return True
        _, (v, a, targets, extra) = plan
        if a.get("cost") == "bonus" and (extra or {}).get("slot"):
            c._bonus_spell = True
        if res:
            self.spend(c, res)
        self.execute(c, a, targets, extra)
        return True

    def pc_turn(self, c):
        c._cs_used = c._ds_used = c._smite_used = c._stun_tried = c._bonus_spell = False
        c._df_used = c._bf_used = c._bm_used = c._hoh_used = c._fw_used = c._da2_used = False
        c._da_bonus = False
        c._as_used = getattr(c, "_as_used", False)
        lam = self.lam(c)
        bonus_used = False
        downed = [x for x in self.allies(c) if not x.dead and x.hp <= 0]
        # 1) Emergency healing with a bonus action (Healing Word, Lay on Hands 2024, potions are actions).
        if downed:
            heals = [o for o in self.pc_options(c, "bonus") if o[2][0] == "spell" and o[2][1][1].get("kind") == "heal"]
            if heals:
                self.do_option(c, max(heals, key=lambda o: o[0]))
                bonus_used = True
            elif self.ed == "2024" and c.resources.get("lay_on_hands", 0) > 0:
                self.lay_on_hands(c, downed[0])
                bonus_used = True
            elif c.resources.get("healing_light", 0) > 0:
                self.healing_light(c, downed[0])
                bonus_used = True
        # 2) Rage (bonus action) when an enemy is close enough to matter.
        if not bonus_used and c.cls == "barbarian" and not c.raging and c.resources.get("rage", 0) > 0:
            near = any(f.up and self.dist(c, f) <= c.speed * 2 + 5 for f in self.foes(c))
            if near and (lam < 6 or self.round <= 1):
                self.spend(c, "rage")
                c.raging = 10
                bonus_used = True
                self.log(f"{c.name} rages")
        # 2b) Subclass bonus-action features.
        if not bonus_used:
            bonus_used = self.subclass_bonus(c, lam)
        # 3) Pre-action bonus spells (Hex, Hunter's Mark, Spiritual Weapon...) when worth it.
        if not bonus_used:
            opts = [o for o in self.pc_options(c, "bonus") if o[0] > 1.5]
            if opts:
                self.do_option(c, max(opts, key=lambda o: o[0]))
                bonus_used = True
        # 4) Channel Divinity options.
        if c.cls == "cleric" and c.resources.get("channel_divinity", 0) > 0 and c.features.get("preserve_life"):
            hurt = [x for x in self.allies(c) if not x.dead and x.hp < x.max_hp * 0.4]
            if len(hurt) >= 2:
                self.spend(c, "channel_divinity")
                pool = c.features["preserve_life"]
                for x in sorted(hurt, key=lambda x: x.hp):
                    amt = min(pool, max(0, x.max_hp // 2 - x.hp))
                    pool -= amt
                    self.heal(x, amt, c)
                self.log(f"{c.name} uses Preserve Life")
                self.end_of_action_bonus(c, bonus_used)
                return
        if c.cls == "cleric" and c.resources.get("channel_divinity", 0) > 0 and c.features.get("twilight_sanctuary") \
                and not c.twilight and lam < 5 and any(f.up for f in self.foes(c)):
            self.spend(c, "channel_divinity")
            c.twilight = 10
            self.log(f"{c.name} uses Twilight Sanctuary")
            self.end_of_action_bonus(c, bonus_used)
            return
        if c.cls == "paladin" and c.features.get("vow_of_enmity") and c.resources.get("channel_divinity", 0) > 0 \
                and (c.vow_target is None or not c.vow_target.up) and lam < 5:
            foes = [x for x in self.foes(c) if x.up and self.dist(c, x) <= c.speed + 10]
            if foes:
                self.spend(c, "channel_divinity")
                c.vow_target = max(foes, key=self.threat)
                self.log(f"{c.name} swears a Vow of Enmity against {c.vow_target.name}")
        if c.cls == "paladin" and c.features.get("sacred_weapon") and c.resources.get("channel_divinity", 0) > 0 and not getattr(c, "sacred", 0) \
                and lam < 4 and any(f.up and self.dist(c, f) <= 35 for f in self.foes(c)):
            self.spend(c, "channel_divinity")
            c.sacred = max(1, c.features.get("sacred_weapon", 1))
            self.log(f"{c.name} uses Sacred Weapon")
            if self.ed == "2024":
                pass  # 2024 Sacred Weapon is part of the Attack action
            else:
                self.end_of_action_bonus(c, bonus_used)
                return
        # 5) Item "slay" effects (e.g. an item that destroys low-level undead).
        for s in c.slay:
            if c.resources.get("item_charge", 0) <= 0:
                break
            vics = [f for f in self.foes(c) if f.up and (not s.get("vs_creature_type") or s["vs_creature_type"] in f.ctype)
                    and f.cr <= s.get("max_cr", 99)]
            if vics and sum(v.hp for v in vics) > 15:
                self.spend(c, "item_charge")
                hit = vics if s.get("area") else [max(vics, key=lambda v: v.hp)]
                self.log(f"{c.name} uses {s.get('name', 'a magic item')} on " + ", ".join(v.name for v in hit))
                for v in hit:
                    sv = s.get("save")
                    if not sv or not self.make_save(v, sv.get("ability", "wis"), sv.get("dc", 15)):
                        self.kill(v, c)
                self.end_of_action_bonus(c, bonus_used)
                return
        # 6) Potion for self or a downed ally when nothing better (counts as an action).
        if c.potions and (c.hp < c.max_hp * 0.25 or (downed and not any(o for o in self.pc_options(c, 'action') if o[2][0] == 'spell' and o[2][1][1].get('kind') == 'heal'))):
            tgt = downed[0] if downed else c
            if self.dist(c, tgt) <= c.speed + 5:
                expr = c.potions.pop()
                self.spend(c, "potion")
                self.move_toward(c, tgt, c.speed)
                self.heal(tgt, roll(self.rng, expr), c)
                self.log(f"{c.name} uses a healing potion on {tgt.name}")
                self.end_of_action_bonus(c, bonus_used)
                return
        # 7) Main action: best value after weighing resource cost.
        self.main_action(c)
        if c.features.get("dread_ambusher") and self.ed == "2014" and not getattr(c, "_da_done", False) and c.actions:
            c._da_done = True
            p = self.plan_for(c, c.actions[0])
            if p:
                c._da_bonus = True
                self.execute(c, p[1], p[2], p[3])
                c._da_bonus = False
        # 8) Fighter Action Surge.
        if c.resources.get("action_surge", 0) > 0 and any(f.up for f in self.foes(c)) and self.lam(c) < 4:
            self.spend(c, "action_surge")
            self.log(f"{c.name} uses Action Surge")
            self.main_action(c)
        self.end_of_action_bonus(c, bonus_used)

    def healing_light(self, c, tgt):
        k = min(c.features.get("healing_light_max", 1), c.resources["healing_light"])
        self.spend(c, "healing_light", k)
        self.heal(tgt, roll(self.rng, f"{k}d6"), c)
        self.log(f"{c.name} uses Healing Light on {tgt.name}")

    def subclass_bonus(self, c, lam):
        """Bonus-action subclass features used at the start of a turn. Returns True if the bonus action was used."""
        f, r = c.features, c.resources
        foes = [x for x in self.foes(c) if x.up]
        if not foes:
            return False
        near = any(self.dist(c, x) <= c.speed * 2 + 5 for x in foes)
        if f.get("starry_archer") and not c.starry and r.get("wild_shape", 0) > 0 and (lam < 6 or self.round <= 1):
            self.spend(c, "wild_shape")
            c.starry = 10
            self.log(f"{c.name} takes Starry Form (Archer)")
            self.starry_shot(c)
            return True
        if r.get("bladesong", 0) > 0 and not any(e.get("name") == "Bladesong" for e in c.effects) and near:
            self.spend(c, "bladesong")
            eff = {"name": "Bladesong", "owner": c, "targets": [c], "rounds": 10, "kind": "buff"}
            c.buffs.append(({"ac": f.get("bladesong_ac", 0)}, eff))
            c.effects.append(eff)
            self.log(f"{c.name} starts Bladesong")
            return True
        if r.get("hexblade_curse", 0) > 0 and (c.cursed is None or not c.cursed.up):
            self.spend(c, "hexblade_curse")
            c.cursed = max(foes, key=lambda x: (self.threat(x), x.hp))
            self.log(f"{c.name} places Hexblade's Curse on {c.cursed.name}")
            return True
        if r.get("fighting_spirit", 0) > 0 and lam < 4 and any(self.dist(c, x) <= c.speed + 5 for x in foes):
            self.spend(c, "fighting_spirit")
            eff = {"name": "Fighting Spirit", "owner": c, "targets": [c], "rounds": 1, "kind": "buff"}
            c.buffs.append(({"advantage_attacks": True}, eff))
            c.effects.append(eff)
            c.temp_hp = max(c.temp_hp, f.get("fighting_spirit_thp", 5))
            self.log(f"{c.name} uses Fighting Spirit")
            return True
        return False

    def starry_shot(self, c):
        foes = [x for x in self.foes(c) if x.up and self.dist(c, x) <= 60 + c.speed]
        t = self.pick_target(c, foes)
        if not t:
            return
        self.move_toward(c, t, c.speed, 60)
        a = {"name": "Starry Archer", "kind": "attack", "melee": False, "range": 60, "is_spell": True,
             "attack_bonus": c.spell_attack, "damage": [{"dice": f"{c.features['starry_archer']}+{c.mods['wis']}", "type": "radiant"}]}
        self.attack_roll(c, a, t, magical=True)

    def lay_on_hands(self, c, tgt):
        if self.dist(c, tgt) > c.speed + 5:
            return
        self.move_toward(c, tgt, c.speed)
        amt = min(c.resources["lay_on_hands"], max(5, tgt.max_hp // 3))
        self.spend(c, "lay_on_hands", amt)
        self.heal(tgt, amt, c)

    def main_action(self, c):
        opts = self.pc_options(c, "action")
        if not opts:
            self.dash(c)
            return
        best = max(opts, key=lambda o: o[0])
        if best[1] <= 0.01:
            self.dash(c)
            return
        self.do_option(c, best)

    def end_of_action_bonus(self, c, bonus_used):
        if bonus_used or not c.up:
            return
        f = c.features
        foes_near = [x for x in self.foes(c) if x.up and self.dist(c, x) <= 5]
        if c.starry:
            self.starry_shot(c)
            return
        if c.resources.get("war_priest", 0) > 0 and foes_near and self.lam(c) < 5 and c.actions:
            self.spend(c, "war_priest")
            t = self.pick_target(c, foes_near)
            self.log(f"{c.name} makes a War Priest attack")
            self.attack_roll(c, c.actions[0], t, magical=c.magic_weapons)
            return
        if c.resources.get("healing_light", 0) > 0:
            hurt = [x for x in self.allies(c) if not x.dead and x.hp < x.max_hp * 0.35]
            if hurt:
                self.healing_light(c, min(hurt, key=lambda x: x.hp / x.max_hp))
                return
        # Spiritual Weapon-style recurring bonus attacks.
        for eff in c.effects:
            rec = eff.get("recurring")
            if rec and rec.get("trigger") == "bonus":
                self.resolve_recurring(c, eff)
                return
        if c.cls == "monk" and foes_near:
            die = f.get("martial_arts_die", "1d6")
            a = {"name": "Martial Arts", "kind": "attack", "melee": True, "range": 5, "weapon": True,
                 "attack_bonus": c.prof + c.mods["dex"], "damage": [{"dice": f"{die}+{c.mods['dex']}", "type": "bludgeoning"}]}
            hits = 1
            if c.resources.get("ki", 0) > 0 and self.lam(c) * RESOURCE_WEIGHTS["ki"] < 0.6 * avg(a["damage"][0]["dice"]):
                self.spend(c, "ki")
                hits = f.get("flurry_hits", 2)
                a["name"] = "Flurry of Blows"
            for _ in range(hits):
                t = self.pick_target(c, [x for x in foes_near if x.up])
                if t:
                    self.attack_roll(c, a, t, magical=True)
            return
        if c.cls == "barbarian" and c.raging and f.get("frenzy") and self.ed == "2014" and foes_near:
            a = c.actions[0]
            t = self.pick_target(c, foes_near)
            if t:
                self.attack_roll(c, a, t, magical=c.magic_weapons)
            return
        if c.cls == "fighter" and c.resources.get("second_wind", 0) > 0 and c.hp < c.max_hp * 0.5:
            self.spend(c, "second_wind")
            self.heal(c, roll(self.rng, f"1d10+{c.level}"), c)
            self.log(f"{c.name} uses Second Wind")
            return
        if c.cls == "bard" and c.resources.get("bardic_inspiration", 0) > 0 and self.lam(c) < 3:
            ally = max([x for x in self.allies(c) if x.up and x is not c] or [c], key=self.threat)
            self.spend(c, "bardic_inspiration")
            eff = {"name": "Bardic Inspiration", "owner": c, "targets": [ally], "rounds": 2, "kind": "buff"}
            ally.buffs.append(({"attack_bonus": f.get("inspiration_die", "1d6")}, eff))
            c.effects.append(eff)
            return
        if c.cls == "paladin" and self.ed == "2024" and c.resources.get("lay_on_hands", 0) > 0:
            hurt = [x for x in self.allies(c) if not x.dead and x.hp < x.max_hp * 0.3]
            if hurt:
                self.lay_on_hands(c, hurt[0])
                return
        if c.cls == "rogue":
            # Cunning Action: disengage from melee if the rogue is fragile.
            return


# ----------------------------------------------------------------------------------------------
# Monte Carlo driver
# ----------------------------------------------------------------------------------------------
def build_sides(edition, party_spec, monster_spec, item_effects, ally_spec=()):
    party, counts = [], {}
    for p in party_spec:
        for i in range(p.get("count", 1)):
            label = p.get("name") or f"{p['cls'].title()} {p['level']}"
            counts[label] = counts.get(label, 0) + 1
            if counts[label] > 1:
                label = f"{label} #{counts[label]}"
            party.append(make_pc(edition, p["cls"], int(p["level"]), label, p.get("subclass")))
    enemies = []
    for m in monster_spec:
        base_name = data.raw(edition, "monsters")[m["index"]]["name"]
        for i in range(int(m.get("count", 1))):
            label = base_name if int(m.get("count", 1)) == 1 else f"{base_name} {i + 1}"
            enemies.append(make_monster(edition, m["index"], label))
    allies = []
    for m in ally_spec:
        base_name = data.raw(edition, "monsters")[m["index"]]["name"]
        for i in range(int(m.get("count", 1))):
            label = f"Ally {base_name}" if int(m.get("count", 1)) == 1 else f"Ally {base_name} {i + 1}"
            allies.append(make_monster(edition, m["index"], label, side="party"))
    apply_item_effects(party + allies + enemies, item_effects)
    return party, enemies, allies


def party_resource_weight(pc, used=None):
    """Weighted resource units (spell slots weigh their level)."""
    total = 0.0
    src = pc.max_slots if used is None else {int(k[4:]): v for k, v in pc.res_used.items() if k.startswith("slot")}
    for lv, n in src.items():
        total += lv * n
    src2 = pc.resources_max if used is None else {k: v for k, v in pc.res_used.items() if not k.startswith("slot")}
    for k, n in src2.items():
        if n >= 99:
            continue
        total += RESOURCE_WEIGHTS.get(k, 1.0) * n
    return total


def simulate(edition, party_spec, monster_spec, settings, n=1000, seed=7, keep_logs=1):
    rng = random.Random(seed)
    party0, enemies0, allies0 = build_sides(edition, party_spec, monster_spec, settings.item_effects, settings.allies)
    outcomes = {"victory": 0, "tpk": 0, "stalemate": 0}
    any_down = multi_down = any_death = 0
    res_frac, hp_frac, rounds = [], [], []
    pc_stats = {p.name: {"dropped": 0, "died": 0, "hp_left": 0.0} for p in party0}
    mon_stats = {m.name: {"killed": 0} for m in enemies0}
    ally_stats = {a.name: {"killed": 0} for a in allies0}
    logs = []
    for i in range(n):
        party = [copy.deepcopy(p) for p in party0]
        enemies = [copy.deepcopy(m) for m in enemies0]
        allies = [copy.deepcopy(a) for a in allies0]
        b = Battle(party + allies, enemies, settings, rng, log=i < keep_logs)
        out = b.run()
        outcomes[out] += 1
        drops = sum(1 for p in party if p.dropped or p.dead)
        any_down += drops >= 1
        multi_down += drops >= 2
        any_death += any(p.dead for p in party)
        tot_w = sum(party_resource_weight(p) for p in party)
        used_w = sum(party_resource_weight(p, used=True) for p in party)
        res_frac.append(used_w / tot_w if tot_w else 0.0)
        hp_frac.append(sum(p.max_hp - max(p.hp, 0) for p in party) / sum(p.max_hp for p in party))
        rounds.append(b.round)
        for p in party:
            st = pc_stats[p.name]
            st["dropped"] += p.dropped or p.dead
            st["died"] += p.dead
            st["hp_left"] += max(p.hp, 0) / p.max_hp
        for m in enemies:
            mon_stats[m.name]["killed"] += m.dead
        for a in allies:
            ally_stats[a.name]["killed"] += a.dead
        if i < keep_logs:
            logs.append(b.lines)
    tot_w = sum(party_resource_weight(p) for p in party0)
    return {
        "n": n,
        "p_victory": outcomes["victory"] / n,
        "p_tpk": outcomes["tpk"] / n,
        "p_stalemate": outcomes["stalemate"] / n,
        "p_any_down": any_down / n,
        "p_multi_down": multi_down / n,
        "p_any_death": any_death / n,
        "resources_used": sum(res_frac) / n,
        "resource_pool": tot_w,
        "hp_expended": sum(hp_frac) / n,
        "avg_rounds": sum(rounds) / n,
        "rounds_hist": rounds,
        "res_hist": res_frac,
        "pcs": {k: {"p_dropped": v["dropped"] / n, "p_died": v["died"] / n, "avg_hp_left": v["hp_left"] / n} for k, v in pc_stats.items()},
        "monsters": {k: {"p_killed": v["killed"] / n} for k, v in mon_stats.items()},
        "allies": {k: {"p_killed": v["killed"] / n} for k, v in ally_stats.items()},
        "sample_log": logs[0] if logs else [],
    }
