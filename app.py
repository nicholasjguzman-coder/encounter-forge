"""Encounter Forge - a D&D 5e / 5.5e encounter playtester.

Run:  streamlit run app.py
"""
import json
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dmsim import agents, compiler, data
from dmsim.classes import CLASSES, build_pc, describe_build
from dmsim.subclasses import NOTES as SUB_NOTES, SUBCLASSES, subclass_level
from dmsim.engine import Settings, Tactics, simulate
from dmsim.llm import BudgetExceeded, Meter, active_model
from dmsim.tuning import TARGETS, distance, on_target, tune

st.set_page_config(page_title="Encounter Forge", page_icon="⚔️", layout="wide")

GOLD, RED, GREEN, GREY = "#c9a227", "#b33a3a", "#4f8a5b", "#8a8077"
st.markdown("""
<style>
h1, h2, h3 { font-family: Georgia, 'Times New Roman', serif; letter-spacing: .3px; }
.block-container { padding-top: 1.6rem; max-width: 1250px; }
div[data-testid="stMetric"] { background: #221c18; border: 1px solid #3a2f27; border-radius: 10px; padding: 10px 14px; }
div[data-testid="stMetricLabel"] p { font-size: .82rem; color: #c8bba8; }
.tier { display:inline-block; padding: 4px 12px; border-radius: 14px; font-weight: 600; margin-right: 8px; }
.small { color: #a89a87; font-size: .85rem; }
div[data-testid="stButton"] button { min-width: 2.5rem; padding-left: .45rem; padding-right: .45rem; }
.safety { border-left: 3px solid #c9a227; padding: 6px 10px; background: #221c18; margin: 4px 0; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

TIER_COLORS = {"Trivial": GREY, "Easy": GREEN, "Medium": GOLD, "Hard": "#d07a2d", "Deadly": RED, "Extremely Deadly": "#7a1f1f",
               "Below Low": GREY, "Low": GREEN, "Moderate": GOLD, "High": "#d07a2d", "Above High": RED}
DEFAULT_PARTY = [("fighter", 5, 1), ("cleric", 5, 1), ("wizard", 5, 1), ("rogue", 5, 1)]
DEFAULT_MONSTERS = {"2014": [("Ogre", 2), ("Goblin", 4)], "2024": [("Ogre", 2), ("Goblin Warrior", 4)]}


# ------------------------------------------------------------------------------------------------
def ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def session_usage():
    return ss("usage", [])


def record_meter(meter):
    session_usage().extend(meter.calls)


def tier_badge(name):
    return f'<span class="tier" style="background:{TIER_COLORS.get(name, GREY)};color:#fff">{name}</span>'


def simulated_tier(r):
    if r["p_tpk"] > TARGETS["Extremely Deadly"]["min_tpk"]:
        return "Extremely Deadly"
    return min(TARGETS, key=lambda k: (distance(r, TARGETS[k]), list(TARGETS).index(k)))


def target_caption(tg):
    if tg.get("min_tpk") and tg["max_tpk"] >= 1:
        tpk = f"TPK above {pct(tg['min_tpk'])}"
    elif tg.get("min_tpk"):
        tpk = f"TPK {pct(tg['min_tpk'])}–{pct(tg['max_tpk'])}"
    else:
        tpk = f"TPK ≤ {pct(tg['max_tpk'])}"
    return f"{tpk}; chance a PC drops: {pct(tg['down_min'])}–{pct(tg['down_max'])}"


def mon_summary(E, monsters):
    by_idx = monster_maps(E)[2]
    return ", ".join(f"{m['count']}× {by_idx.get(m['index'], (m['index'],))[0]}" for m in monsters)


def new_version(label, monsters, start_rounds, surprise, results, settings, target=None, rationale=""):
    return {"label": label, "monsters": monsters, "start_rounds": start_rounds, "surprise": surprise,
            "results": results, "settings": settings, "tier": simulated_tier(results), "target": target,
            "on_target": on_target(results, TARGETS[target]) if target else None, "rationale": rationale}


def add_version(R, ver):
    ver["num"] = len(R["log"]) + 1
    R["log"].append(ver)
    return ver


def show_version(E, R, ver):
    """Make a logged version the one displayed (no re-simulation needed)."""
    R.update(monsters=ver["monsters"], start_rounds=ver["start_rounds"], surprise=ver["surprise"],
             results=ver["results"], settings=ver["settings"], current=ver["num"])
    R["official"] = data.official_difficulty(E, [p["level"] for p in R["party"] for _ in range(p["count"])],
                                             [data.monster_base(E, m["index"])["xp"] for m in ver["monsters"] for _ in range(m["count"])])
    R["approx"] = agents.approximations(E, ver["monsters"])


def creature_rows(E, prefix, rows, v, by_idx, noun, add_label):
    """Row editor for a list of SRD creatures (monster or ally): dropdown, count, remove button."""
    mwidths = [2.6, 0.8, 0.55]
    mon_ids = [i for _, i, _ in data.monster_names(E)]
    if rows:
        for h, label in zip(st.columns(mwidths), [noun, "Count", ""]):
            h.caption(label)
    out, remove = [], None
    for row in rows:
        k = f"{E}_{prefix}{row['id']}_{v}"
        cc = st.columns(mwidths, vertical_alignment="center")
        idx = cc[0].selectbox(noun, mon_ids, index=mon_ids.index(row["index"]) if row["index"] in mon_ids else 0,
                              format_func=lambda i: f"{'★ ' if data.is_custom(i) else ''}{by_idx[i][0]} (CR {by_idx[i][1]})", key=f"{k}_idx",
                              label_visibility="collapsed")
        cnt = cc[1].number_input("Count", 1, 20, int(row["count"]), key=f"{k}_cnt", label_visibility="collapsed")
        if cc[2].button("✕", key=f"{k}_del", help=f"Remove this {noun.lower()}", width="stretch"):
            remove = row["id"]
        row.update(index=idx, count=int(cnt))
        out.append({"index": idx, "count": int(cnt)})
    if remove is not None:
        rows[:] = [r for r in rows if r["id"] != remove]
        st.rerun()
    if st.button(add_label, key=f"{E}_add{prefix}"):
        rows.append({"id": max([r["id"] for r in rows] or [-1]) + 1,
                     "index": rows[-1]["index"] if rows else ("brown-bear" if "brown-bear" in mon_ids else mon_ids[0]),
                     "count": 1})
        st.rerun()
    if out:
        st.caption(" · ".join(f"{m['count']}× {by_idx[m['index']][0]} (CR {by_idx[m['index']][1]})" for m in out))
    return out


def custom_creature_panel(E):
    """Homebrew / non-SRD creatures: the Rules Compiler agent builds a stat block from the DM's description."""
    customs = data.custom_creatures(E)
    with st.expander(f"🧪 Custom creatures ({len(customs)})", expanded=False):
        st.caption("Need a creature that isn't in the free SRD, or a homebrew one? Describe it in your own words "
                   "(or type its stats). The Rules Compiler agent builds a full stat block, filling in anything "
                   "you leave out. It then appears with a ★ in the Monster and Ally lists.")
        name = st.text_input("Creature name", key=f"{E}_cname", placeholder="e.g. Needle Blight")
        desc = st.text_area("Description or stats", key=f"{E}_cdesc", height=100,
                            placeholder="e.g. Medium plant, CR 1/4. AC 12, 11 HP. Claw +3 to hit, 2d4+1 piercing. "
                                        "Ranged needles +3, 30/60 ft, 2d6+1 piercing. Can't be blinded.")
        if st.button("🧪 Build this creature", key=f"{E}_cbuild", disabled=not use_ai):
            if not (name.strip() and desc.strip()):
                st.warning("Give the creature a name and a description first.")
                return
            clean_n, ev1 = agents.guard_text(name, "custom creature name")
            clean_d, ev2 = agents.guard_text(desc, "custom creature description")
            ss(f"{E}_safety", []).extend(ev1 + ev2)
            meter = Meter(cap_usd=cap, label=f"custom-{int(time.time())}")
            try:
                with st.spinner("Rules Compiler is building the stat block..."):
                    idx, raw_entry, combat, assumptions = compiler.compile_custom(E, clean_n, clean_d, meter)
                data.save_custom(E, idx, raw_entry, combat)
                st.session_state[f"{E}_cnote"] = (raw_entry["name"], assumptions, ev1 + ev2)
            except BudgetExceeded as e:
                st.error(f"🛡️ {e}")
            except Exception as e:
                st.error(f"Couldn't build that creature: {e}")
            finally:
                record_meter(meter)
            st.rerun()
        note = st.session_state.pop(f"{E}_cnote", None)
        if note:
            st.success(f"Built **{note[0]}**. It's now in the Monster and Ally lists." + (f" Filled in: {note[1]}" if note[1] else ""))
            if note[2]:
                st.markdown('<div class="safety">🛡️ Instruction-like text was removed before the Rules Compiler read it.</div>',
                            unsafe_allow_html=True)
        for idx, c in customs.items():
            r_, comb = c["raw"], c["combat"]
            acts = ", ".join(a["name"] for a in comb.get("actions", [])) or "—"
            cc = st.columns([5, 1], vertical_alignment="center")
            cc[0].markdown(f"**★ {r_['name']}** · CR {r_['challenge_rating']} · AC {r_['armor_class'][0]['value']} · "
                           f"HP {r_['hit_points']} · {r_['size']} {r_['type']}  \n<span class='small'>Actions: {acts}</span>",
                           unsafe_allow_html=True)
            if cc[1].button("✕", key=f"{E}_cdel_{idx}", help="Delete this custom creature", width="stretch"):
                data.delete_custom(E, idx)
                R = st.session_state.get(f"{E}_result")
                if R and any(m["index"] == idx for ver in R["log"] for m in ver["monsters"] + (ver["settings"].allies or [])):
                    st.session_state.pop(f"{E}_result", None)  # its results referenced the deleted creature
                for key in (f"{E}_mon_rows", f"{E}_ally_rows"):
                    rows = st.session_state.get(key, [])
                    rows[:] = [r for r in rows if r["index"] != idx]
                st.rerun()


def load_into_form(E, ver):
    st.session_state[f"{E}_mon_rows"] = [{"id": i, "index": m["index"], "count": m["count"]} for i, m in enumerate(ver["monsters"])]
    st.session_state[f"{E}_dist_init"] = ver["start_rounds"]
    st.session_state[f"{E}_surprise_init"] = ver["surprise"]
    st.session_state[f"{E}_v"] = st.session_state.get(f"{E}_v", 0) + 1


def monster_maps(edition):
    names = data.monster_names(edition)
    by_name = {n: i for n, i, _ in names}
    labels = [f"{n}" for n, _, _ in names]
    return by_name, labels, {i: (n, cr) for n, i, cr in names}


def pct(x):
    return f"{x * 100:.0f}%" if x >= 0.01 or x == 0 else "<1%"


# ------------------------------------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ⚔️ Encounter Forge")
    st.caption("Playtest a D&D combat thousands of times before your players ever see it.")
    n_sims = st.select_slider("Simulated fights per run", options=[200, 500, 1000, 2000], value=1000)
    use_ai = st.toggle("Use AI agents", value=True,
                       help="Turn off to run with rules only (no model calls). The numbers always come from the simulator, never the model.")
    cap = st.number_input("Spending cap per run (USD)", min_value=0.001, max_value=2.0, value=0.25, step=0.05, format="%.3f",
                          help="Every model call is metered; the run stops cleanly if the cap would be exceeded.")
    st.divider()
    usage_box = st.empty()
    st.divider()
    with st.expander("How it works"):
        st.markdown(
            "1. **Encounter Parser** (AI) turns a plain-English description into a party and monster list.\n"
            "2. **Items & Effects** (AI) converts magic items into checked, capped modifiers.\n"
            "3. **Tactician** (AI) decides how each side fights.\n"
            "4. **Simulator** (code) plays the fight out many times: real dice, every stat-block action, "
            "spells, conditions, concentration, legendary actions, death saves.\n"
            "5. **Explainer** (AI) briefs you; **Tuner** (AI) proposes changes and the simulator re-checks them.\n\n"
            "Rules data: SRD 5.1 (2014) and SRD 5.2 (2024), CC-BY-4.0, compiled once into a combat schema by the "
            "**Rules Compiler** agent.")
    with st.expander("Assumptions"):
        st.markdown(
            "- Quick-build PCs: primary stat 17 (+1 at levels 4 and 8), Con 14, typical armor, the SRD subclass, "
            "popular combat spells. No magic items unless you list them.\n"
            "- Positioning is a single line: the two sides start a set number of rounds apart. "
            "No terrain, cover or opportunity attacks.\n"
            "- Area spells hit the number of targets from the DMG *Targets in Areas of Effect* table.\n"
            "- PCs start with full resources. **Resources used** weighs each spell slot by its level plus class "
            "features, potions and item charges (Hit Dice excluded). **HP expended** is reported separately.\n"
            "- A **TPK** means every PC is at 0 HP or dead at the same time (and, if you add allies, every ally is down too).\n"
            "- Allies are SRD creatures on the party's side. They act like monsters and aren't counted in the official XP rating.")


# ------------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------------
st.markdown("# ⚔️ Encounter Forge")
st.markdown('<span class="small">Will this fight kill my party? Simulate it, read the odds, and tune it to the difficulty you want.</span>',
            unsafe_allow_html=True)

tabs = st.tabs([f"🐉 {data.EDITIONS['2014']}", f"🛡️ {data.EDITIONS['2024']}"])


def render(edition):
    E = edition
    by_name, labels, by_idx = monster_maps(E)
    v = ss(f"{E}_v", 0)  # bump to reset widgets after the parser fills the form

    # ---- 1. Describe -------------------------------------------------------------------------
    with st.expander("✍️ Describe the encounter in plain English (optional)", expanded=False):
        desc = st.text_area("Description", key=f"{E}_desc", height=90, label_visibility="collapsed",
                            placeholder="e.g. Four level-3 adventurers (paladin, ranger, bard, warlock) stumble into a "
                                        "bugbear and four goblins about 90 feet away in a dark forest; the goblins have the drop on them.")
        if st.button("Fill in the form", key=f"{E}_parse", disabled=not use_ai):
            clean, events = agents.guard_text(desc, "description")
            ss(f"{E}_safety", []).extend(events)
            meter = Meter(cap_usd=cap, label=f"parse-{int(time.time())}")
            try:
                with st.spinner("Encounter Parser is reading your description..."):
                    parsed = agents.parse_encounter(E, clean, meter)
                if parsed["party"]:
                    st.session_state[f"{E}_party_rows"] = [
                        {"id": i, "cls": p["cls"], "sub": p.get("subclass"), "level": p["level"], "count": p["count"]}
                        for i, p in enumerate(parsed["party"])]
                if parsed["monsters"]:
                    st.session_state[f"{E}_mon_rows"] = [{"id": i, "index": m["index"], "count": m["count"]}
                                                         for i, m in enumerate(parsed["monsters"])]
                if parsed.get("allies"):
                    st.session_state[f"{E}_ally_rows"] = [{"id": i, "index": m["index"], "count": m["count"]}
                                                          for i, m in enumerate(parsed["allies"])]
                if parsed["start_rounds"] is not None:
                    st.session_state[f"{E}_dist_init"] = parsed["start_rounds"]
                if parsed["surprise"]:
                    st.session_state[f"{E}_surprise_init"] = parsed["surprise"]
                st.session_state[f"{E}_v"] = v + 1
                st.session_state[f"{E}_parse_note"] = parsed
            except BudgetExceeded as e:
                st.error(str(e))
            finally:
                record_meter(meter)
            st.rerun()
        note = st.session_state.get(f"{E}_parse_note")
        if note:
            msg = note.get("notes") or "Form filled from your description."
            if note.get("unmatched"):
                msg += f"  Couldn't match: {', '.join(note['unmatched'])}."
            st.info(msg)
        if any(e["field"] == "description" for e in st.session_state.get(f"{E}_safety", [])):
            st.markdown('<div class="safety">🛡️ Some instruction-like text was removed from your description before the AI read it.</div>',
                        unsafe_allow_html=True)

    # ---- 2. Party & monsters -----------------------------------------------------------------
    c1, c2 = st.columns([1.45, 1], gap="large")
    with c1:
        st.markdown("### 🧙 The party")
        widths = [1.25, 1.75, 0.8, 0.8, 0.45]
        rows = ss(f"{E}_party_rows", [{"id": i, "cls": c, "sub": None, "level": l, "count": n}
                                      for i, (c, l, n) in enumerate(DEFAULT_PARTY)])
        for h, label in zip(st.columns(widths), ["Class", "Subclass", "Level", "Count", ""]):
            h.caption(label)
        party, remove = [], None
        for row in rows:
            k = f"{E}_p{row['id']}_{v}"
            cc = st.columns(widths, vertical_alignment="center")
            cls = cc[0].selectbox("Class", CLASSES, index=CLASSES.index(row["cls"]), format_func=str.title,
                                  key=f"{k}_cls", label_visibility="collapsed")
            subs = SUBCLASSES[E][cls]
            # The subclass widget is keyed by class, so switching class shows only that class's subclasses.
            sub = cc[1].selectbox("Subclass", subs, index=subs.index(row["sub"]) if row["sub"] in subs else 0,
                                  format_func=lambda x, d=subs[0]: f"{x} (SRD)" if x == d else x,
                                  key=f"{k}_sub_{cls}", label_visibility="collapsed")
            lvl = cc[2].number_input("Level", 1, 20, int(row["level"]), key=f"{k}_lvl", label_visibility="collapsed")
            cnt = cc[3].number_input("Count", 1, 8, int(row["count"]), key=f"{k}_cnt", label_visibility="collapsed")
            if cc[4].button("✕", key=f"{k}_del", help="Remove this character", width="stretch"):
                remove = row["id"]
            row.update(cls=cls, sub=sub, level=int(lvl), count=int(cnt))
            party.append({"cls": cls, "level": int(lvl), "count": int(cnt), "subclass": None if sub == subs[0] else sub})
        if remove is not None:
            rows[:] = [r for r in rows if r["id"] != remove]
            st.rerun()
        if st.button("➕ Add a character", key=f"{E}_addpc"):
            rows.append({"id": max([r["id"] for r in rows] or [-1]) + 1, "cls": "fighter", "sub": None,
                         "level": rows[-1]["level"] if rows else 5, "count": 1})
            st.rerun()
        with st.expander("See the quick builds"):
            for p in party:
                b = build_pc(E, p["cls"], p["level"], subclass=p["subclass"])
                sc = b["features"]["subclass"]
                late = p["level"] < subclass_level(E, p["cls"])
                st.markdown(f"**{p['cls'].title()} {p['level']}** ({sc}) — {describe_build(b)}  \n"
                            + (f"<span class='small'>{SUB_NOTES[sc]}</span>  \n" if sc in SUB_NOTES else "")
                            + (f"<span class='small'>Subclass features start at level {subclass_level(E, p['cls'])}.</span>  \n" if late else "")
                            + f"<span class='small'>Spells: {', '.join(s['name'] for s in b['spells']) or '—'}</span>",
                            unsafe_allow_html=True)
    with c2:
        st.markdown("### 👹 The monsters")
        default_rows = [{"id": i, "index": by_name[n], "count": k} for i, (n, k) in enumerate(DEFAULT_MONSTERS[E])]
        monsters = creature_rows(E, "mon", ss(f"{E}_mon_rows", default_rows), v, by_idx, "Monster", "➕ Add a monster")
        st.markdown("### 🤝 Allies")
        st.caption("Creatures fighting on the party's side: companions, summons, hirelings (optional).")
        allies = creature_rows(E, "ally", ss(f"{E}_ally_rows", []), v, by_idx, "Ally", "➕ Add an ally")
        custom_creature_panel(E)

    # ---- 3. Conditions ----------------------------------------------------------------------
    st.markdown("### 🗺️ Battlefield & extras")
    d1, d2 = st.columns([1, 1], gap="large")
    with d1:
        start_rounds = st.slider("Starting distance (rounds of dashing for a speed-30 creature)", 0, 5,
                                 ss(f"{E}_dist_init", 1), key=f"{E}_dist_{v}",
                                 help="0 = already in melee. Each round is about 60 feet.")
        st.caption(f"≈ {max(5, start_rounds * 60)} feet apart")
        sur_opts = {"none": "No surprise", "party": "The party is surprised", "monsters": "The monsters are surprised"}
        surprise = st.radio("Surprise", list(sur_opts), format_func=sur_opts.get, horizontal=True,
                            index=list(sur_opts).index(ss(f"{E}_surprise_init", "none")), key=f"{E}_sur_{v}")
        st.caption("2014: surprised creatures lose their first turn. 2024: surprised creatures roll initiative with disadvantage.")
    with d2:
        items_text = st.text_area("Magic items or effects in play (optional)", key=f"{E}_items", height=80,
                                  placeholder="e.g. Fighter has a +1 longsword; the cleric's mace destroys undead of CR 1 or lower "
                                              "(3 charges); the party carries 3 potions of healing.")
        tactics_text = st.text_area("Tactics notes (optional)", key=f"{E}_tactics", height=68,
                                    placeholder="e.g. The goblins flee when the bugbear dies; the cultists focus the healer.")

    R0 = st.session_state.get(f"{E}_result")
    same_enc = bool(R0) and R0["party"] == party and R0["monsters"] == monsters and R0.get("allies", []) == allies
    changed = same_enc and (start_rounds != R0["start_rounds"] or surprise != R0["surprise"]
                            or items_text != R0.get("items_text", "") or tactics_text != R0.get("tactics_text", ""))
    b1, b2 = st.columns([1, 2.2])
    run = b1.button("🎲 Run the playtest", type="primary", key=f"{E}_run", disabled=not (party and monsters))
    quick = b2.button("⚡ Re-simulate with these battlefield & extras changes", key=f"{E}_quick", disabled=not changed,
                      help="Keeps the same party and monsters and re-runs only the simulation (plus the Items or Tactician "
                           "agent if you edited that box). Adds a row to the version history.")
    if R0 and not same_enc:
        b2.caption("Party, monsters or allies changed since the last run: use 🎲 Run the playtest.")
    if run:
        run_playtest(E, party, monsters, start_rounds, surprise, items_text, tactics_text, allies)
    if quick:
        quick_resim(E, R0, start_rounds, surprise, items_text, tactics_text)
    res = st.session_state.get(f"{E}_result")
    if res:
        show_results(E, res)


def run_playtest(E, party, monsters, start_rounds, surprise, items_text, tactics_text, allies=()):
    allies = list(allies)
    meter = Meter(cap_usd=cap, label=f"run-{int(time.time())}")
    safety = []
    tactics = {"monster_targeting": "random", "finish_downed": False, "flee_at": 0.0, "per_monster": {},
               "party_targeting": "focus_weakest", "resource_policy": "balanced", "rationale": "Default tactics (AI off)."}
    items = {"effects": [], "ignored": [], "notes": "", "events": []}
    status = st.status("Running the playtest...", expanded=True)
    try:
        if use_ai:
            if items_text.strip():
                clean, ev = agents.guard_text(items_text, "magic items")
                safety += ev
                status.write("🪄 Items & Effects agent is reading your magic items...")
                names = []
                for p in party:
                    for k in range(p["count"]):
                        nm = f"{p['cls'].title()} {p['level']}"
                        names.append(nm if k == 0 else f"{nm} #{k + 1}")
                items = agents.interpret_items(E, clean, names, meter)
                safety += items["events"]
            clean_t, ev = agents.guard_text(tactics_text, "tactics notes")
            safety += ev
            status.write("🧠 Tactician is deciding how each side fights...")
            tactics = agents.tactician(E, party, monsters, clean_t, meter, allies=allies)
        settings = Settings(edition=E, start_rounds=start_rounds, surprise=surprise,
                            tactics=Tactics(**{k: v for k, v in tactics.items() if k != "rationale"}),
                            item_effects=items["effects"], allies=allies)
        status.write(f"🎲 Simulating {n_sims:,} fights...")
        t0 = time.time()
        r = simulate(E, party, monsters, settings, n=n_sims)
        sim_s = time.time() - t0
        official = data.official_difficulty(E, [p["level"] for p in party for _ in range(p["count"])],
                                            [data.monster_base(E, m["index"])["xp"] for m in monsters for _ in range(m["count"])])
        approx = agents.approximations(E, monsters)
        briefing = ""
        if use_ai:
            status.write("📜 Explainer is writing your briefing...")
            briefing = agents.explain(E, party, monsters, r, official, tactics, approx, meter, allies=allies)
        status.update(label=f"Done: {n_sims:,} fights in {sim_s:.1f}s", state="complete", expanded=False)
    except BudgetExceeded as e:
        status.update(label="Stopped: spending cap reached", state="error")
        safety.append({"field": "budget", "type": "budget_cap", "detail": str(e)})
        st.error(f"🛡️ {e} The run was stopped before spending more.")
        record_meter(meter)
        return
    except Exception as e:
        status.update(label="Something went wrong", state="error")
        st.exception(e)
        record_meter(meter)
        return
    record_meter(meter)
    prev = st.session_state.get(f"{E}_result")
    if prev and prev["party"] == party and prev.get("allies", []) == allies:  # same side: keep the version history going
        R = prev
        R["safety"] = R["safety"] + safety
        R["meter"] = R["meter"] + meter.calls
    else:
        R = {"party": party, "log": [], "safety": safety, "meter": meter.calls, "tune_errors": []}
    R.update(tactics=tactics, items=items, items_text=items_text, tactics_text=tactics_text, sim_s=sim_s, allies=allies)
    ver = add_version(R, new_version("Playtest", monsters, start_rounds, surprise, r, settings))
    show_version(E, R, ver)
    R["briefing"], R["briefing_for"] = briefing, ver["num"]
    st.session_state[f"{E}_result"] = R


def quick_resim(E, R, start_rounds, surprise, items_text, tactics_text):
    """Re-simulate the same party and monsters with new battlefield / extras, reusing agent results where possible."""
    meter = Meter(cap_usd=cap, label=f"quick-{int(time.time())}")
    changes = []
    if start_rounds != R["start_rounds"]:
        changes.append(f"distance {R['start_rounds']}→{start_rounds} rd")
    if surprise != R["surprise"]:
        changes.append(f"surprise: {surprise}")
    try:
        with st.spinner("Re-simulating..."):
            if items_text != R.get("items_text", ""):
                changes.append("magic items")
                R["items"] = {"effects": [], "ignored": [], "notes": "", "events": []}
                if use_ai and items_text.strip():
                    clean, ev = agents.guard_text(items_text, "magic items")
                    R["safety"] = R["safety"] + ev
                    names = []
                    for p in R["party"]:
                        for k in range(p["count"]):
                            nm = f"{p['cls'].title()} {p['level']}"
                            names.append(nm if k == 0 else f"{nm} #{k + 1}")
                    R["items"] = agents.interpret_items(E, clean, names, meter)
                    R["safety"] = R["safety"] + R["items"]["events"]
            if tactics_text != R.get("tactics_text", ""):
                changes.append("tactics")
                if use_ai:
                    clean_t, ev = agents.guard_text(tactics_text, "tactics notes")
                    R["safety"] = R["safety"] + ev
                    R["tactics"] = agents.tactician(E, R["party"], R["monsters"], clean_t, meter, allies=R.get("allies", []))
            t = R["tactics"]
            settings = Settings(edition=E, start_rounds=start_rounds, surprise=surprise,
                                tactics=Tactics(**{k: v for k, v in t.items() if k != "rationale"}),
                                item_effects=R["items"]["effects"], allies=R.get("allies", []))
            t0 = time.time()
            r = simulate(E, R["party"], R["monsters"], settings, n=n_sims)
            R["sim_s"] = time.time() - t0
    except BudgetExceeded as e:
        R["safety"] = R["safety"] + [{"field": "budget", "type": "budget_cap", "detail": str(e)}]
        st.error(f"🛡️ {e}")
        record_meter(meter)
        return
    record_meter(meter)
    R["meter"] = R["meter"] + meter.calls
    R.update(items_text=items_text, tactics_text=tactics_text)
    ver = add_version(R, new_version("Changed " + ", ".join(changes), R["monsters"], start_rounds, surprise, r, settings))
    show_version(E, R, ver)


def metric_row(r):
    row1, row2 = st.columns(3), st.columns(3)
    cols = row1 + row2
    cols[0].metric("TPK chance", pct(r["p_tpk"]), help="Every PC at 0 HP or dead at the same time")
    cols[1].metric("1+ PCs at 0 HP", pct(r["p_any_down"]), help="Chance at least one PC drops to 0 HP during the fight")
    cols[2].metric("2+ PCs at 0 HP", pct(r["p_multi_down"]), help="Chance two or more different PCs drop to 0 HP")
    cols[3].metric("Resources used", pct(r["resources_used"]), help="Spell slots (weighted by level), class features, potions and item charges. HP not included.")
    cols[4].metric("HP expended", pct(r["hp_expended"]), help="Share of the party's total HP missing when the fight ends")
    cols[5].metric("Avg. rounds", f"{r['avg_rounds']:.1f}")


def show_results(E, R):
    r, off = R["results"], R["official"]
    st.divider()
    st.markdown(f"## 📊 Results <span class='small'>· version #{R.get('current', 1)}: {mon_summary(E, R['monsters'])}, "
                f"start {R['start_rounds']} rd, surprise: {R['surprise']}"
                + (f", allies: {mon_summary(E, R['allies'])}" if R.get("allies") else "") + "</span>", unsafe_allow_html=True)
    tier = simulated_tier(r)
    st.markdown(
        f"Simulated difficulty: {tier_badge(tier)} &nbsp; Official XP rating: {tier_badge(off['rating'])}"
        f"<span class='small'>{off['basis']}: {off['adjusted_xp']:,.0f} XP vs thresholds "
        + ", ".join(f"{k} {v:,}" for k, v in off["thresholds"].items()) + "</span>", unsafe_allow_html=True)
    st.write("")
    metric_row(r)
    st.caption(f"Based on {r['n']:,} simulated fights · party wins {pct(r['p_victory'])} · any PC dies {pct(r['p_any_death'])}"
               + (f" · stalemate {pct(r['p_stalemate'])}" if r["p_stalemate"] > 0.005 else ""))

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        if R["briefing"]:
            st.markdown("#### 📜 The Explainer's briefing")
            if R.get("briefing_for") != R.get("current"):
                st.caption(f"This briefing describes version #{R.get('briefing_for')}; you're viewing #{R.get('current')}.")
                if use_ai and st.button("📜 Refresh the briefing for this version", key=f"{E}_rebrief"):
                    meter = Meter(cap_usd=cap, label=f"brief-{int(time.time())}")
                    try:
                        with st.spinner("Explainer is writing your briefing..."):
                            R["briefing"] = agents.explain(E, R["party"], R["monsters"], r, off, R["tactics"], R["approx"], meter,
                                                           allies=R.get("allies", []))
                        R["briefing_for"] = R["current"]
                    except BudgetExceeded as e:
                        st.error(f"🛡️ {e}")
                    record_meter(meter)
                    R["meter"] = R["meter"] + meter.calls
                    st.rerun()
            st.markdown(R["briefing"])
        st.markdown("#### Who is in danger?")
        names = list(r["pcs"])
        fig = go.Figure()
        fig.add_bar(y=names, x=[r["pcs"][n]["p_dropped"] for n in names], orientation="h", name="Drops to 0 HP",
                    marker_color=GOLD, text=[pct(r["pcs"][n]["p_dropped"]) for n in names], textposition="outside")
        fig.add_bar(y=names, x=[r["pcs"][n]["p_died"] for n in names], orientation="h", name="Dies",
                    marker_color=RED, text=[pct(r["pcs"][n]["p_died"]) for n in names], textposition="outside")
        fig.update_layout(barmode="group", height=60 + 55 * len(names), margin=dict(l=0, r=30, t=10, b=0),
                          xaxis=dict(tickformat=".0%", range=[0, 1.1]), paper_bgcolor="rgba(0,0,0,0)",
                          plot_bgcolor="rgba(0,0,0,0)", font_color="#efe6d8", legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown("#### How long do fights last?")
        hist = pd.Series(r["rounds_hist"]).value_counts().sort_index()
        fig2 = go.Figure(go.Bar(x=hist.index, y=hist.values / r["n"], marker_color=GOLD))
        fig2.update_layout(height=210, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Rounds",
                           yaxis=dict(tickformat=".0%"), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           font_color="#efe6d8")
        st.plotly_chart(fig2, width="stretch")
        st.markdown("#### Monsters defeated")
        mk = r["monsters"]
        st.dataframe(pd.DataFrame([{"Monster": k, "Defeated in": pct(v["p_killed"])} for k, v in mk.items()]),
                     hide_index=True, width="stretch", height=min(250, 38 + 35 * len(mk)))
        ak = r.get("allies") or {}
        if ak:
            st.markdown("#### 🤝 Allies lost")
            st.dataframe(pd.DataFrame([{"Ally": k, "Killed in": pct(v["p_killed"])} for k, v in ak.items()]),
                         hide_index=True, width="stretch", height=min(250, 38 + 35 * len(ak)))

    with st.expander("🧠 Tactics, items and approximations"):
        t = R["tactics"]
        st.markdown(f"**Tactician:** monsters target *{t['monster_targeting'].replace('_', ' ')}*, "
                    f"{'attack downed PCs' if t['finish_downed'] else 'ignore downed PCs'}, "
                    f"{'flee at ' + pct(t['flee_at']) + ' HP' if t['flee_at'] else 'fight to the death'}; "
                    f"party targets *{t['party_targeting'].replace('_', ' ')}* and plays *{t['resource_policy']}* with resources.  \n"
                    + "".join(f"*{monster_maps(E)[2].get(i, (i,))[0]}*: " + ", ".join(
                        f"{k.replace('_', ' ')} {pct(v) if k == 'flee_at' else str(v).replace('_', ' ')}" for k, v in cfg.items()) + "  \n"
                        for i, cfg in t.get("per_monster", {}).items())
                    + f"<span class='small'>{t['rationale']}</span>", unsafe_allow_html=True)
        if R["items"]["effects"]:
            st.markdown("**Items & effects applied:**")
            for e in R["items"]["effects"]:
                details = {k: v for k, v in e.items() if k not in ("applies_to", "type", "name")}
                st.markdown(f"- {e.get('name', e['type'])} → *{e['applies_to']}*: `{e['type']}` {json.dumps(details) if details else ''}")
        if R["items"]["ignored"]:
            st.caption("No combat effect: " + "; ".join(map(str, R["items"]["ignored"])))
        if R["approx"]:
            st.markdown("**Approximated by the simulator:**")
            for a in R["approx"][:15]:
                st.markdown(f"- <span class='small'>{a}</span>", unsafe_allow_html=True)

    with st.expander("🎬 Watch one simulated fight (turn-by-turn log)"):
        st.code("\n".join(r["sample_log"]) or "(no log)", language=None)

    if R["safety"]:
        with st.expander(f"🛡️ Safety events ({len(R['safety'])})", expanded=True):
            for e in R["safety"]:
                st.markdown(f'<div class="safety"><b>{e["type"].replace("_", " ")}</b> — {e.get("field", "")}: {e["detail"]}</div>',
                            unsafe_allow_html=True)

    # ---- Tuning ------------------------------------------------------------------------------
    st.markdown("### 🎯 Tune it")
    t1, _ = st.columns([1, 1.4])
    with t1:
        target_name = st.selectbox("Target difficulty", list(TARGETS), index=2, key=f"{E}_target")
        tg = TARGETS[target_name]
        st.caption(target_caption(tg))
        if on_target(r, tg):
            st.success("The version you're viewing is already on target.")
        go_tune = st.button("🔧 Tune the encounter", key=f"{E}_tune", disabled=not use_ai or on_target(r, tg))
        st.caption("Starts from the version you're viewing. Every attempt is added to the version history.")
    if go_tune:
        meter = Meter(cap_usd=cap, label=f"tune-{int(time.time())}")
        bar = st.progress(0.0, text="Tuner is proposing changes...")
        try:
            attempts = tune(E, R["party"], R["monsters"], R["settings"], r, target_name, tg, meter,
                            progress=lambda i, n: bar.progress(i / n, text=f"Attempt {i} of {n}: proposing and re-simulating..."))
        finally:
            record_meter(meter)
            bar.empty()
        R["meter"] = R["meter"] + meter.calls
        added = []
        for a in attempts[1:]:
            if "error" in a:
                R["tune_errors"].append(a["error"])
                continue
            s2 = R["settings"]
            settings = Settings(edition=E, start_rounds=a["start_rounds"], surprise=a["surprise"], max_rounds=s2.max_rounds,
                                tactics=s2.tactics, item_effects=s2.item_effects, allies=s2.allies)
            added.append(add_version(R, new_version(f"Tuner → {target_name}", a["monsters"], a["start_rounds"], a["surprise"],
                                                    a["results"], settings, target=target_name, rationale=a.get("rationale", ""))))
        if added:
            best = min(added, key=lambda x: distance(x["results"], tg))
            st.session_state[f"{E}_loadsel"] = best["num"]
    with st.container():
        st.markdown("#### 📚 Version history")
        rows = []
        for ver in R["log"]:
            rr = ver["results"]
            rows.append({
                "#": ver["num"], "": "👁️" if ver["num"] == R.get("current") else "", "Version": ver["label"],
                "Monsters": mon_summary(E, ver["monsters"]),
                "Setup": f"{ver['start_rounds']} rd" + ("" if ver["surprise"] == "none" else f", {ver['surprise']} surprised"),
                "Difficulty": ver["tier"], "TPK": pct(rr["p_tpk"]),
                "1+ down": pct(rr["p_any_down"]), "2+ down": pct(rr["p_multi_down"]),
                "Resources": pct(rr["resources_used"]),
                "On target": "" if ver["on_target"] is None else ("✅" if ver["on_target"] else "—"),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(420, 38 + 35 * len(rows)))
        st.caption("👁️ = the version shown above. Load any version to view its full results and copy it into the form — no re-run needed.")
        nums = [ver["num"] for ver in R["log"]]
        l1, l2 = st.columns([2.5, 1], vertical_alignment="bottom")
        if st.session_state.get(f"{E}_loadsel") not in nums:
            st.session_state[f"{E}_loadsel"] = R.get("current", nums[-1])
        pick = l1.selectbox("Version", nums, key=f"{E}_loadsel",
                            format_func=lambda n: f"#{n} · {R['log'][n - 1]['label']} · {mon_summary(E, R['log'][n - 1]['monsters'])}")
        if l2.button("Load this version", key=f"{E}_load", disabled=pick == R.get("current")):
            ver = R["log"][pick - 1]
            show_version(E, R, ver)
            load_into_form(E, ver)
            st.rerun()
        notes = [ver for ver in R["log"] if ver.get("rationale")]
        if notes:
            with st.expander("Why the Tuner made each change"):
                for ver in notes:
                    st.markdown(f"<span class='small'><b>#{ver['num']}</b> ({ver['target']}): {ver['rationale']}</span>",
                                unsafe_allow_html=True)
        for err in R.get("tune_errors", []):
            st.warning(f"🛡️ {err}")

    # ---- Token usage for this run -----------------------------------------------------------
    with st.expander("🪙 Tokens & cost for this run"):
        calls = R["meter"]
        if not calls:
            st.caption("No model calls (AI agents off).")
        else:
            df = pd.DataFrame(calls)
            g = df.groupby("agent")[["input", "cached", "output", "cost", "seconds"]].sum().reset_index()
            g["cost"] = g["cost"].map(lambda x: f"${x:.4f}")
            g = g.rename(columns={"agent": "Agent", "input": "Input tokens", "cached": "Cached", "output": "Output tokens",
                                  "cost": "Cost (USD)", "seconds": "Seconds"})
            st.dataframe(g, hide_index=True, width="stretch")
            st.caption(f"Total: {df['input'].sum():,} input ({df['cached'].sum():,} cached) + {df['output'].sum():,} output tokens · "
                       f"\\${df['cost'].sum():.4f} · the simulation itself: {R['sim_s']:.1f}s of CPU and \\$0 in tokens")


with tabs[0]:
    render("2014")
with tabs[1]:
    render("2024")

with usage_box.container():
    use = session_usage()
    tot = {k: sum(c[k] for c in use) for k in ("input", "cached", "output", "cost")}
    st.markdown("**This session's model usage**")
    st.caption(f"Model: `{active_model()}` via Portkey")
    st.markdown(f"{len(use)} calls · {tot['input']:,} input ({tot['cached']:,} cached) · {tot['output']:,} output tokens  \n"
                f"**\\${tot['cost']:.4f}** estimated")
