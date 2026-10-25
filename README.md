# ⚔️ Encounter Forge — a D&D encounter playtester

**Will this fight kill my party?** Encounter Forge simulates a Dungeons & Dragons combat encounter
1,000+ times and reports:

- the chance of a **total party kill (TPK)**
- the chance that **at least one PC**, and that **two or more PCs**, drop to 0 HP
- the share of the party's **resources** used (spell slots, rage, ki, Action Surge, potions, item charges…)
- the share of the party's **HP** expended

It compares these numbers with the official XP rating. It can also **tune** an encounter to a target difficulty:
an AI agent proposes changes and the simulator re-checks each one.
You can also add **allies**: SRD creatures fighting on the party's side.
Every playtest, battlefield tweak and tuning attempt is kept in a **version history**. You can load any version
back into the form without re-running it.

Both rule sets are supported, each in its own tab: **5e (2014)** and **5.5e (2024)**.

---

## Run it (about 3 minutes)

Requires Python 3.10 or newer.

```bash
git clone <this repo>
cd encounter-forge
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

**API key.** The app looks for a `.env` file in this folder first, then in the folder one level above.
It needs only one key:

```
PORTKEY_API_KEY=...
```

See `.env.example`. With no key, switch **Use AI agents** off in the sidebar. The simulator, charts and
official difficulty ratings all still work, because every number comes from code, not from the model.

**Try it.** Press **🎲 Run the playtest** with the default party (fighter, cleric, wizard and rogue, all level 5)
against 2 ogres and 4 goblins. Then choose a target difficulty and press **🔧 Tune the encounter**. You can also
open **✍️ Describe the encounter in plain English** and type something like *"Three level-2 adventurers are
ambushed by a bugbear and three goblins at 30 feet."*

Run the offline tests (no API key needed):

```bash
python -m pytest -q tests
```

---

## How it works

```
 Plain-English description ──► [Encounter Parser]  ─┐
 Magic items / effects     ──► [Items & Effects]   ─┤  validated, clamped JSON
 DM tactics notes          ──► [Tactician]         ─┤
                                                    ▼
                         ┌──────────────────────────────────────────┐
  SRD stat blocks/spells │  Monte Carlo SIMULATOR (pure Python)     │
  compiled once by the ─►│  dice · attacks · saves · spells ·       │──► results ──► [Explainer] ──► briefing
  [Rules Compiler]       │  conditions · concentration · legendary  │
                         │  actions · death saves · resources       │
                         └──────────────────────────────────────────┘
                                         ▲            │
                                         └─[Tuner]◄───┘  propose a change → re-simulate → repeat
```

| Agent | Role | Output |
|---|---|---|
| **Rules Compiler** (offline) | Turns every SRD monster action and spell into one combat schema | Validated JSON cached in `data/compiled/` |
| **Encounter Parser** | Turns a plain-English encounter into party and monster lists | Monsters matched to SRD ids |
| **Items & Effects** | Turns magic items and effects into modifiers | Typed, clamped effects |
| **Tactician** | Decides how each side fights: targeting, fleeing, resource use | Tactics settings |
| **Tuner** | Proposes encounter changes to hit a target difficulty | A new monster lineup, checked by the simulator |
| **Explainer** | Writes a short briefing for the DM from the measured results | Markdown |

All models are called through Portkey (`gpt-5.6-luna`). **No model ever rolls dice or produces a statistic.**

### Safety
- **Prompt-injection guard.** Free text is scanned, and instruction-like content is removed *before* a model sees it.
  The model is also told that user text is data, inside `<user_data>` tags.
- **Validated outputs.** Every agent's JSON is checked by code. Unknown monsters, spells and effect types are
  rejected. Bonuses are capped (for example, +3 at most to attack). The Tuner can't change the party or add more
  than 30 monsters.
- **Spending cap.** Each run has a dollar cap and a model-call limit, which you set in the sidebar. A run that would
  exceed the cap stops cleanly.
- **Token logging.** Every call's input, cached-input and output tokens are logged to `logs/usage.jsonl` and shown in the app.

### Subclasses
Each party row has a **Subclass** column. The SRD subclass (Champion, Life, Evoker…) is the default. Popular
official subclasses are also available (for example Battle Master, Assassin, Gloom Stalker, Light, Twilight,
Hexblade, Bladesinging, Divination, Zealot, Vengeance). For these, the app includes only our own encoding of
their combat mechanics in `dmsim/subclasses.py` (numbers and short labels), not rulebook text. Each edition's tab
offers the subclasses that exist in that edition.

### Assumptions and simplifications
- Quick-build PCs: primary ability 17 (+1 at levels 4 and 8), Con 14, typical armor, the chosen subclass, and a
  curated list of popular combat spells. PCs have no magic items unless you list them.
- One-dimensional positioning: the two sides start *N rounds of dashing* apart (about 60 ft per round).
  There is no terrain, cover or opportunity attacks.
- Area spells hit the number of targets from the DMG *Targets in Areas of Effect* table.
- A few unusual abilities are approximated or skipped. The app lists them under *Tactics, items and approximations*.

---

## Project layout

```
app.py                 Streamlit UI (two tabs: 2014 and 2024 rules)
dmsim/engine.py        Monte Carlo combat simulator
dmsim/classes.py       Quick-build PCs for 12 classes, levels 1-20, both editions
dmsim/subclasses.py    Subclass options and their mechanics
dmsim/agents.py        Parser, Items & Effects, Tactician, Tuner, Explainer, safety guard
dmsim/tuning.py        Closed tuning loop and target difficulty bands
dmsim/compiler.py      Rules Compiler agent (offline)
dmsim/llm.py           Portkey client, token logging, spending cap
dmsim/data.py          SRD loading and official difficulty tables (2014 thresholds, 2024 budgets)
data/raw/              SRD 5.1 and 5.2 monsters and spells (from dnd5eapi.co)
data/compiled/         Combat schema produced by the Rules Compiler
tools/fetch_srd.py     Re-download SRD data
tools/compile_srd.py   Re-compile SRD data (resumable)
tests/                 Offline tests
```

## Data and license

This work includes material from the System Reference Document 5.1 ("SRD 5.1") and the System Reference
Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at
https://www.dndbeyond.com/srd. Both are licensed under the Creative Commons Attribution 4.0 International
License (https://creativecommons.org/licenses/by/4.0/legalcode). Data was retrieved from the 5e SRD API
(https://www.dnd5eapi.co).

Dungeons & Dragons is a trademark of Wizards of the Coast. This is unofficial, non-commercial fan content.
Non-SRD subclasses are represented only by our own simplified encoding of their game mechanics.
