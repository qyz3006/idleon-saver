# PRD — idleon-saver Rewrite (Simple PRD)

**Project Name**: `idleon_saver_rewrite`
**Language**: English (spec/code) — follows user requirement
**Original Requirement Recap**: Full rewrite of the `idleon-saver` Python desktop tool for *Legends of Idleon*. Preserve the local-decode core (reads the game's LevelDB saves, decodes the Haxe/Stencyl `mySave` blob losslessly). Rebuild the parsing/domain-model layer + multi-format export layer borrowing architecture patterns from three reference projects (IdleOnAutoReviewBot, IdleonToolbox, idleon-efficiency). Modernize packaging (installable `pyproject`), keep & modernize the Kivy desktop GUI, add a full `pytest` suite. Export to all four community formats: IdleonCompanion, Cogstruction, IdleonToolbox, IdleonEfficiency.

---

## 1. Product Goal

Give *Legends of Idleon* players a reliable, installable desktop tool that reads their **local game save files from disk**, decodes them losslessly, and exports the data to **all four** major community tools (IdleonCompanion, Cogstruction, IdleonToolbox, IdleonEfficiency) — via a modernized Kivy GUI or a clean CLI — without crashing when static game-data is missing.

---

## 2. User Stories

| ID | As a… | I want… | so that… |
|----|-------|---------|----------|
| US-01 | idleon player | to point the tool at my `LegendsOfIdleon` install so it finds and reads the local LevelDB save | I can decode my own save without manual file hunting |
| US-02 | idleon player | the Stencyl `mySave` blob decoded to/from JSON losslessly | I can inspect, edit, and re-inject my save without corruption |
| US-03 | non-technical user | a Kivy GUI that walks me from Start → locate exe → export buttons | I can export without using a terminal |
| US-04 | IdleonCompanion user | my decoded save exported to IdleonCompanion format | I can use the Companion planner on my real data |
| US-05 | Cogstruction user | my decoded save exported to Cogstruction (`cog_datas.csv` + `empties_datas.csv`) | I can plan constructions from my save |
| US-06 | IdleonToolbox user | my save exported as raw cloud-save JSON | I can import it directly into the Toolbox website |
| US-07 | IdleonEfficiency user | my save exported as raw cloud-save JSON (`Cloudsave`) | I can import it directly into IdleonEfficiency |
| US-08 | power user / scripter | CLI subcommands (`decode`, `encode`, `export`, `gui`) | I can automate or script the tool in my own workflows |
| US-09 | player on a fresh checkout | the tool to load with empty/missing static game-data and warn instead of crash | I can still decode/export even before vendored data is present |

---

## 3. Requirement Pool

> Reference-pattern tags: **ARB** = IdleOnAutoReviewBot (defensive `safe_get`/`safe_convert`, exception hierarchy, freshness selection, data models); **TB** = IdleonToolbox (pure `getXxx(data)` parsers, cascading `serializeData`, schema migrations, `tryToParse`); **IE** = idleon-efficiency (DDD `Domain` base class, generic `EfficiencyEngine`, `safeJsonParse`, env switching, stores). **CORE** = existing idleon-saver decode/export code to preserve/port.

### P0 — Must have (core flows + existing tests keep passing)
- **P0-1** Preserve `idleon_saver/ldb.py` (plyvel LevelDB reader) verbatim. *[CORE]*
- **P0-2** Preserve `idleon_saver/stencyl/{common,decoder,encoder}.py` verbatim; decode↔encode must stay lossless (round-trip). *[CORE]* (`test_stencyl.py`, `test_scripts.py` assert this)
- **P0-3** Port `Exporter` ABC with `LocalExporter` (decoded `local.json` shape) and `FirebaseExporter` (raw `firebase.json` shape) into a new `exporters/` package. *[CORE → port]*
- **P0-4** IdleonCompanion export: port `to_idleon_companion()` dict (alchemy, starSigns, cards, stamps, statues, checklist, chars) as-is. *[CORE → port]*
- **P0-5** Cogstruction export: port `to_cogstruction()` → `cog_datas.csv` + `empties_datas.csv` as-is. *[CORE → port]*
- **P0-6** Port CLI scripts (`decode.py`, `encode.py`, `inject.py`+`inject.js`, `mangle.py`, `trim_save.py`) keeping functionality, modernizing imports. *[CORE → port]*
- **P0-7** Keep `tests/` behaviors: `test_stencyl`, `test_scripts`, `test_export`, `test_gui` + real fixtures `tests/data/{local.json,firebase.json,stencylsave.txt}`. All must pass after rewrite. *[CORE]*
- **P0-8** Installable packaging via `pyproject.toml` (Poetry-compatible), entry-point exposing the CLI. *[modernize]*
- **P0-9** Keep hardcoded constants in `idleon_saver/data/__init__.py` (skill_names, starsign_ids, constellation_names, cog maps) — confirmed correct. *[CORE]*

### P1 — Should have (the 2 new exporters + modern CLI + defensive data)
- **P1-1** IdleonToolbox exporter: re-emit raw cloud-save JSON (flat `key→JSON-string` shape = `firebase.json`). For firebase-source: pass-through. For local-source: best-effort unwrap of decoded JSON (documented limitation). *[IE/TB pattern]*
- **P1-2** IdleonEfficiency exporter: same raw cloud-save `Cloudsave` JSON re-emit as Toolbox. *[IE pattern]*
- **P1-3** Defensive data loading (ARB `safe_get`/`safe_convert` + `tryToParse`): vendor needed JSON into `idleon_saver/data/vendored/`; if a file is missing, use empty defaults + `log.warning`, never raise. *[ARB / TB]*
- **P1-4** Rebuild parsing/domain-model layer as pure parser functions per game system (e.g. `getXxx(data, …)`) + defensive conversions, so exporters consume a stable intermediate model. *[TB / IE / ARB]*
- **P1-5** Modern unified CLI with subcommands `decode`, `encode`, `export`, `gui`; replace ad-hoc `scripts/*.py` argparse with a single `typer`/argparse entry. *[modernize]*
- **P1-6** Custom exception hierarchy + graceful freshness/multi-source selection for decoded data. *[ARB]*

### P2 — Nice to have
- **P2-1** Richer DDD domain model (`Domain` base w/ `getRawKeys`/`init`/`parse`) wrapping each game system. *[IE]*
- **P2-2** Schema versioning + migrations for the intermediate model (3-pass cascading `serializeData` for bonuses). *[TB]*
- **P2-3** Fixture-driven / generated type stubs for the domain model. *[TB]*
- **P2-4** Local/remote env switching + optional store layer for cached decoded state. *[IE]*

---

## 4. UI Sketch Notes

### Kivy Desktop GUI (modernized, keep ScreenManager flow)
```
[Start Screen]
   └─ Button: "Locate LegendsOfIdleon"
          │  (file browser → finds install / LevelDB dir)
          ▼
[Main Screen]
   ├─ Shows detected save path + decode status
   ├─ "Decode / Reload Save"  (runs ldb + stencyl decode)
   └─ Export panel (4 buttons):
        • Export → IdleonCompanion
        • Export → Cogstruction (2 CSVs)
        • Export → IdleonToolbox (raw cloud-save JSON)
        • Export → IdleonEfficiency (raw cloud-save JSON)
   └─ Status/toast area (success / warning if data missing)
```
- Keep `gui/main.py` + `main.kv` ScreenManager structure; modernize widgets/layout, keep "find exe → export" flow intact.
- Missing vendored data → show non-blocking warning toast, never block the screen.

### CLI (single entry point, P1-5)
```
idleon-saver decode   [--source local|firebase] [--out decoded.json]
idleon-saver encode   [--in decoded.json] [--out stencylsave.txt]
idleon-saver export   [--format companion|cogstruction|toolbox|efficiency] [--in ...] [--out ...]
idleon-saver gui      # launch Kivy desktop app
```
- `decode`/`encode` mirror P0-2/P0-6 round-trip; `export` dispatches to the `exporters/` package (P0-3/P0-4/P0-5/P1-1/P1-2).

---

## 5. Resolved Open Questions

**User-locked decisions (resolved — do not re-ask):**
- **RQ-1 (Scope):** Full rewrite — keep local-decode core (`ldb.py` + `stencyl/` codec) **verbatim**; rebuild parsing/domain-model + export layer using reference patterns; modernize packaging (installable `pyproject`) + add full `pytest`. ✅ Resolved.
- **RQ-2 (Export formats):** All four — IdleonCompanion, Cogstruction, IdleonToolbox, IdleonEfficiency. ✅ Resolved.
- **RQ-3 (UI):** Keep the Kivy desktop GUI; modernize, do not drop. ✅ Resolved.

**Design calls (resolved during context research):**
- **RQ-4 (Static data strategy):** Vendor needed game-data JSON into `idleon_saver/data/vendored/` (replacing the empty git submodules `idleon-data/` + `IdleonWikiBot/`) and load **defensively** — missing file → empty default + `log.warning`, never crash. ✅ Resolved.
- **RQ-5 (Toolbox / Efficiency export semantics):** Both exporters **re-emit the raw cloud-save JSON** (`firebase.json` shape: flat `key→JSON-string`). Firebase-source saves = pass-through; local-source saves = best-effort unwrapped decoded JSON (documented limitation). This matches what the two websites actually ingest. ✅ Resolved.
- **RQ-6 (Constants):** Existing hardcoded constants in `idleon_saver/data/__init__.py` are correct and kept as-is. ✅ Resolved.
