"""Dice parsing and rolling. All randomness in the simulator goes through here (no model ever rolls dice)."""
import re
from functools import lru_cache

_TERM = re.compile(r"([+-]?)\s*(?:(\d*)d(\d+)|(\d+))")


@lru_cache(maxsize=4096)
def parse(expr):
    """'2d10+8' -> (((2, 10),), 8). Unparseable input counts as 0."""
    if expr is None:
        return ((), 0)
    if isinstance(expr, (int, float)):
        return ((), int(expr))
    dice, flat = [], 0
    for sign, n, sides, num in _TERM.findall(str(expr).replace(" ", "")):
        s = -1 if sign == "-" else 1
        if sides:
            dice.append((s * int(n or 1), int(sides)))
        elif num:
            flat += s * int(num)
    return (tuple(dice), flat)


@lru_cache(maxsize=4096)
def avg(expr):
    dice, flat = parse(expr)
    return sum(n * (s + 1) / 2 for n, s in dice) + flat


@lru_cache(maxsize=4096)
def dice_avg(expr):
    """Average of the dice part only (what doubles on a critical hit)."""
    dice, _ = parse(expr)
    return sum(n * (s + 1) / 2 for n, s in dice)


def roll(rng, expr, crit=False, mult=1):
    dice, flat = parse(expr)
    total = flat
    for n, s in dice:
        count = abs(n) * mult * (2 if crit else 1)
        t = sum(rng.randint(1, s) for _ in range(count))
        total += t if n > 0 else -t
    return max(total, 0)


def scale(expr, mult):
    """Multiply the number of dice ('1d10' * 2 -> '2d10'), keeping flat modifiers."""
    dice, flat = parse(expr)
    parts = [f"{n * mult}d{s}" for n, s in dice]
    if flat:
        parts.append(str(flat))
    return "+".join(parts) or "0"


def d20(rng, adv=0):
    a = rng.randint(1, 20)
    if adv == 0:
        return a
    b = rng.randint(1, 20)
    return max(a, b) if adv > 0 else min(a, b)
