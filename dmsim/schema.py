"""Normalized combat schema shared by monsters, spells and class features.

The Rules Compiler agent turns raw stat-block / spell text into these objects once;
the simulator only ever reads this schema. Pydantic validation rejects malformed output.
"""
from typing import Literal, Optional

import re

from pydantic import BaseModel as _PydanticBase, Field, field_validator, model_validator

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
CONDITIONS = (
    "blinded", "charmed", "frightened", "incapacitated", "paralyzed", "petrified",
    "poisoned", "prone", "restrained", "stunned", "unconscious", "grappled",
    "banished", "outlined", "slowed",
)
DAMAGE_TYPES = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
)


class BaseModel(_PydanticBase):
    """Lenient base: drops nulls (so defaults apply) and coerces sloppy integers like "self" or "60 feet"."""

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data):
        if not isinstance(data, dict):
            return data
        out = {}
        for k, v in data.items():
            if v is None or k not in cls.model_fields:
                continue
            ann = cls.model_fields[k].annotation
            if ann is int and not isinstance(v, int):
                if isinstance(v, bool):
                    v = int(v)
                else:
                    m = re.search(r"-?\d+", str(v))
                    v = int(m.group()) if m else 0
            out[k] = v
        return out


class Damage(BaseModel):
    dice: str  # e.g. "2d10+8", "7", "3d6"
    type: str = "force"

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        v = (v or "force").lower()
        return v if v in DAMAGE_TYPES else "force"


class Save(BaseModel):
    ability: str = "dex"
    dc: Optional[int] = None  # None = use the caster's spell save DC
    half: bool = False  # half damage on a successful save

    @field_validator("ability")
    @classmethod
    def _ab(cls, v):
        v = (v or "dex").lower()[:3]
        return v if v in ABILITIES else "dex"


class Area(BaseModel):
    shape: str = "sphere"  # sphere, cone, cube, line, cylinder, emanation
    size: int = 20  # feet (radius for spheres/cylinders, length for cones/lines)


class Condition(BaseModel):
    name: str
    duration: int = 10  # rounds
    save_ends: bool = True  # target repeats the save at end of each of its turns
    save_ability: Optional[str] = None
    breaks_on_damage: bool = False  # e.g. Sleep, Hypnotic Pattern

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = (v or "").lower()
        return v if v in CONDITIONS else "incapacitated"


class Buff(BaseModel):
    attack_bonus: Optional[str] = None  # "1d4" or "2"
    save_bonus: Optional[str] = None
    damage_bonus: Optional[str] = None  # added to each weapon/spell hit
    ac: int = 0
    advantage_attacks: bool = False
    extra_attack: bool = False
    resistances: list[str] = Field(default_factory=list)
    attack_penalty: Optional[str] = None  # debuff, e.g. Bane "1d4"
    save_penalty: Optional[str] = None


class Recurring(BaseModel):
    """Damage that repeats each round after the initial cast (Spiritual Weapon, Spirit Guardians...)."""
    trigger: Literal["bonus", "action", "auto"] = "auto"
    damage: list[Damage] = Field(default_factory=list)
    save: Optional[Save] = None
    attack: bool = False  # uses a spell attack roll
    targets: int = 1


class Uses(BaseModel):
    type: Literal["at_will", "per_day", "recharge", "per_rest"] = "at_will"
    times: int = 1
    recharge_min: int = 6  # recharge on a d6 roll >= this


class Upcast(BaseModel):
    dice: Optional[str] = None  # extra dice per slot level above base
    extra_targets: int = 0
    extra_rolls: int = 0


class Ability(BaseModel):
    name: str
    cost: Literal["action", "bonus", "reaction", "none"] = "action"
    kind: Literal["attack", "save", "auto", "heal", "buff", "debuff", "control", "summon", "utility"] = "attack"
    melee: bool = False
    range: int = 5
    rolls: int = 1  # attack rolls (Scorching Ray = 3) or missiles (Magic Missile = 3)
    attack_bonus: Optional[int] = None  # None = caster's spell attack bonus
    damage: list[Damage] = Field(default_factory=list)
    save: Optional[Save] = None
    targets: int = 1
    area: Optional[Area] = None
    condition: Optional[Condition] = None
    heal: Optional[str] = None
    temp_hp: Optional[str] = None
    buff: Optional[Buff] = None  # applied to allies (or to enemies, for debuffs)
    concentration: bool = False
    duration: int = 1  # rounds
    recurring: Optional[Recurring] = None
    uses: Optional[Uses] = None
    upcast: Optional[Upcast] = None
    cantrip_scaling: bool = False
    support: Literal["full", "approx", "none"] = "full"
    notes: str = ""


class LegendaryAction(BaseModel):
    cost: int = 1
    uses_action: Optional[str] = None  # name of an existing action it repeats (e.g. "Claw")
    ability: Optional[Ability] = None


class Traits(BaseModel):
    magic_resistance: bool = False  # advantage on saves vs spells
    pack_tactics: bool = False
    regeneration: int = 0
    regen_blocked_by: list[str] = Field(default_factory=list)
    undead_fortitude: bool = False
    legendary_resistance: int = 0
    damage_aura: Optional[Ability] = None  # damage to adjacent enemies each turn
    sneak_attack: Optional[str] = None  # extra dice once per turn
    reaction_ac: int = 0  # Parry
    notes: str = ""


class MonsterCombat(BaseModel):
    """The part of a monster the LLM compiles; the rest is read directly from the data."""
    actions: list[Ability] = Field(default_factory=list)
    multiattack: list[dict[str, int]] = Field(default_factory=list)  # options: {"Bite": 1, "Claw": 2}

    @field_validator("multiattack", mode="before")
    @classmethod
    def _multi(cls, v):
        out = []
        for opt in v or []:
            if isinstance(opt, dict):
                fixed = {}
                for k, n in opt.items():
                    if isinstance(n, int):
                        fixed[k] = n
                    else:  # "1d4" style counts -> their average, rounded
                        m = re.match(r"(\d*)d(\d+)", str(n))
                        fixed[k] = round(int(m.group(1) or 1) * (int(m.group(2)) + 1) / 2) if m else int(re.sub(r"\D", "", str(n)) or 1)
                out.append(fixed)
        return out
    bonus_actions: list[Ability] = Field(default_factory=list)
    reactions: list[Ability] = Field(default_factory=list)
    legendary_per_round: int = 0
    legendary_actions: list[LegendaryAction] = Field(default_factory=list)
    traits: Traits = Field(default_factory=Traits)
    spells: list[dict] = Field(default_factory=list)  # [{"name": "Fireball", "uses": "at_will"|"per_day"|"slot", "times": 1, "level": 3}]
    spell_dc: Optional[int] = None
    spell_attack: Optional[int] = None
    spell_slots: dict[str, int] = Field(default_factory=dict)


class SpellCombat(BaseModel):
    ability: Ability
