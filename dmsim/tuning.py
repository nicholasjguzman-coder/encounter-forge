"""Closed-loop tuning: the Tuner agent proposes, the simulator checks, repeat until on target."""
from .agents import tuner_propose
from .engine import Settings, simulate
from .llm import BudgetExceeded

TARGETS = {
    "Easy": {"max_tpk": 0.01, "down_min": 0.0, "down_max": 0.10},
    "Medium": {"max_tpk": 0.03, "down_min": 0.10, "down_max": 0.30},
    "Hard": {"max_tpk": 0.10, "down_min": 0.30, "down_max": 0.60},
    "Deadly": {"max_tpk": 0.50, "min_tpk": 0.10, "down_min": 0.60, "down_max": 1.0},
    "Extremely Deadly": {"max_tpk": 1.0, "min_tpk": 0.50, "down_min": 0.50, "down_max": 1.0},  # TPK above 50%
}


def on_target(r, t):
    return (r["p_tpk"] <= t["max_tpk"] and r["p_tpk"] >= t.get("min_tpk", 0)
            and t["down_min"] <= r["p_any_down"] <= t["down_max"])


def distance(r, t):
    """How far results are from the target band (0 = on target)."""
    d = 0.0
    d += max(0, r["p_tpk"] - t["max_tpk"]) * 3 + max(0, t.get("min_tpk", 0) - r["p_tpk"]) * 3
    d += max(0, t["down_min"] - r["p_any_down"]) + max(0, r["p_any_down"] - t["down_max"])
    return d


def tune(edition, party, monsters, settings, base_results, target_name, target, meter, max_iters=4, n=400,
         progress=None):
    """Returns list of attempts: dicts with monsters, start_rounds, surprise, results, rationale, on_target."""
    history = []
    attempts = [{"monsters": monsters, "start_rounds": settings.start_rounds, "surprise": settings.surprise,
                 "results": base_results, "rationale": "Your original encounter.", "on_target": on_target(base_results, target),
                 "distance": distance(base_results, target)}]
    cur = attempts[0]
    for i in range(max_iters):
        if cur["on_target"]:
            break
        if progress:
            progress(i + 1, max_iters)
        try:
            prop = tuner_propose(edition, party, cur["monsters"], cur["start_rounds"], cur["surprise"], cur["results"],
                                 {"name": target_name, **target}, history, meter, allies=settings.allies)
        except BudgetExceeded as e:
            attempts.append({"error": str(e)})
            break
        except Exception as e:  # invalid proposal: record and let the agent try again
            history.append({"proposal": "invalid", "error": str(e)[:200]})
            continue
        s2 = Settings(edition=edition, start_rounds=prop["start_rounds"], surprise=prop["surprise"],
                      max_rounds=settings.max_rounds, tactics=settings.tactics, item_effects=settings.item_effects,
                      allies=settings.allies)
        r = simulate(edition, [dict(p) for p in party], prop["monsters"], s2, n=n, seed=11 + i)
        att = {**prop, "results": r, "on_target": on_target(r, target), "distance": distance(r, target)}
        attempts.append(att)
        history.append({"monsters": prop["monsters"], "start_rounds": prop["start_rounds"], "surprise": prop["surprise"],
                        "p_tpk": round(r["p_tpk"], 3), "p_any_down": round(r["p_any_down"], 3)})
        if att["distance"] <= cur["distance"] or att["on_target"]:
            cur = att
        else:
            cur = att  # keep exploring from the latest proposal; best is picked at the end
    return attempts
