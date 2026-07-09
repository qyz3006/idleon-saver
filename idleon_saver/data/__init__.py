"""Vendored static game-data for idleon-saver.

This module loads the committed JSON data that the exporters depend on. It is
written to be **defensive**: when a file or even the whole directory is missing
(on a fresh checkout, or when the optional wiki data has not been vendored yet)
the module logs a warning and falls back to empty defaults rather than raising.
Every derivation below uses ``.get(key, default)`` so a missing wiki file can
never crash the tool at import time (this was the root cause of the old
``KeyError: 'Statue'`` import crash).

Exposed names (stable surface used by the rest of the codebase)::

    idleon_data, wiki_data, skill_names, starsign_ids, starsign_names,
    constellation_names, cog_datas_map, cog_boosts, cog_type_map, Bags,
    bag_maps, statues, card_reqs, vial_names, stamp_names, stamps,
    pouch_names, pouch_sizes
"""

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from idleon_saver.log import get_logger
from idleon_saver.utility import ROOT_DIR

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent
_VENDORED_DIR = _DATA_DIR / "vendored"


def _load_json_files(directory: Path, recursive: bool = False) -> dict:
    """Glob JSON files in *directory* into a ``{stem: parsed}`` mapping.

    Missing directory or unreadable file -> logged + skipped, never raises.
    Each loaded object has its ``__comment`` key stripped, and if it has a
    top-level ``data`` key we unwrap to that (so maps files with a ``data``
    envelope behave like the raw payload).

    Args:
        directory: Directory to glob.
        recursive: When ``True`` glob ``**/*.json`` (used for wiki data).

    Returns:
        Mapping of filename stem -> parsed JSON value.
    """
    result: dict = {}
    if not directory.exists():
        logger.warning(
            "Vendored data directory missing; using empty data: %s", directory
        )
        return result

    pattern = "**/*.json" if recursive else "*.json"
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as file:
                jsondata: Any = json.load(file)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read vendored data file %s: %s", path, exc)
            continue

        try:
            del jsondata["__comment"]
        except (KeyError, TypeError):
            pass

        result[path.stem] = jsondata.get("data", jsondata)
    return result


# --- Vendored static game-data --------------------------------------------
# `idleon_data` comes from the committed `vendored/maps/*.json`.
idleon_data: dict = _load_json_files(_VENDORED_DIR / "maps")

# `wiki_data` comes from `vendored/wiki/**/*.json` (optional). On a normal
# checkout this directory does not exist yet, so it is simply empty.
wiki_data: dict = _load_json_files(_VENDORED_DIR / "wiki", recursive=True)


# --- Statues ---------------------------------------------------------------
# vendored `statueList.json` is `{"data": [<display name>, ...]}`; each entry
# is concatenated into a "<name> Statue" string (e.g. "Power Statue Statue").
_statue_list = idleon_data.get("statueList", [])
if not isinstance(_statue_list, list):
    logger.warning("statueList data is not a list; statues will be empty")
    _statue_list = []
statues = [f"{name} Statue" for name in _statue_list if isinstance(name, str)]

# --- Skill names (hardcoded, order-sensitive) -----------------------------
# Skill names MUST stay in the exact order of the save data's `Lv0` skill-level
# array (index 0 = Character, 1 = Mining, ...). The export logic zips this list
# 1:1 against each character's `Lv0` array, so any mismatch mislabels skills.
#
# W7 ("Shimmerfin Deep") added two skills:
#   - Spelunking (W7 Part I)  -> Lv0 index 19
#   - Research   (W7 Part II) -> Lv0 index 20
# The W4-W6 skills (indices 10-18) are included so the new W7 skills land at the
# correct positions. When a new world adds skills, APPEND them here in `Lv0` order.
skill_names = [
    "Character",   # 0
    "Mining",      # 1
    "Smithing",    # 2
    "Choppin'",    # 3
    "Fishing",     # 4
    "Alchemy",     # 5
    "Catching",    # 6
    "Trapping",    # 7
    "Construction",  # 8
    "Worship",     # 9
    "Cooking",     # 10  - W4 (Hyperion Nebula)
    "Breeding",    # 11  - W4
    "Laboratory",  # 12  - W4
    "Sailing",     # 13  - W5 (Smolderin' Plateau)
    "Divinity",    # 14  - W5
    "Gaming",      # 15  - W5
    "Farming",     # 16  - W6 (Spirited Valley)
    "Sneaking",    # 17  - W6
    "Summoning",   # 18  - W6
    "Spelunking",  # 19  - W7 Part I (Shimmerfin Deep)
    "Research",    # 20  - W7 Part II (Shimmerfin Deep)
]

# --- Card requirements -----------------------------------------------------
# vendored `cards.json` is `{"data": [tier_lists]}` where each tier list holds
# dicts with `name` and `amountPerTier`. Map internal card name -> per-tier req.
_card_lists = idleon_data.get("cards", [])
if not isinstance(_card_lists, list):
    _card_lists = []
card_reqs: dict = {}
for _tier in _card_lists:
    if not isinstance(_tier, list):
        continue
    for _card in _tier:
        if isinstance(_card, dict) and "name" in _card:
            card_reqs[_card["name"]] = _card.get(
                "amountPerTier", _card.get("perTier")
            )

# --- Vial names ------------------------------------------------------------
# vendored `alchemy.json` -> `vials` list of dicts each with a `name` field.
_alchemy = idleon_data.get("alchemy", {})
if not isinstance(_alchemy, dict):
    _alchemy = {}
_vials = _alchemy.get("vials", [])
if not isinstance(_vials, list):
    _vials = []
vial_names = [
    v["name"] for v in _vials if isinstance(v, dict) and "name" in v
]

# --- Stamps ----------------------------------------------------------------
# vendored `stampList.json` -> `{"Combat": [...], "Skill": [...], "Misc": [...]}`
# each a list of stamp display names. `stamp_names` is the 3-group structure the
# exporters zip against each character's stamp levels; `stamps` mirrors it.
_stamp_list = idleon_data.get("stampList", {})
if not isinstance(_stamp_list, dict):
    _stamp_list = {}
stamp_names = [
    list(_stamp_list.get("Combat", []) or []),
    list(_stamp_list.get("Skill", []) or []),
    list(_stamp_list.get("Misc", []) or []),
]
# `get_stamps` only consumes `stamp_names`, but we keep `stamps` aligned for API
# stability.
stamps = stamp_names


class Bags(Enum):
    """Bag categories used by the checklist / inventory exporters."""

    INV = "inventory"
    GEM = "gem_shop"
    STORAGE = "storage"


def _index_to_name(items: Any) -> dict:
    """Build a ``{str(index): name}`` mapping from a list of bag dicts."""
    result: dict = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, dict) and "index" in item and "name" in item:
            result[str(item["index"])] = item["name"]
    return result


_bags = idleon_data.get("bags", {})
if not isinstance(_bags, dict):
    _bags = {}
# GEM (gem-shop) bags have no vendored source; use an empty placeholder so
# downstream code (e.g. the checklist) never crashes. Documented limitation:
# gem-shop bag display names are wiki-only data.
bag_maps = {
    Bags.INV: _index_to_name(_bags.get("inventory", [])),
    Bags.GEM: {},
    Bags.STORAGE: _index_to_name(_bags.get("storage", [])),
}

# --- Pouch names (hardcoded) ----------------------------------------------
pouch_names = {
    "Mining": "Mining",
    "Chopping": "Choppin",
    "Foods": "Food",
    "bCraft": "Materials",
    "Fishing": "Fish",
    "Bugs": "Bug",
    "Critters": "Critter",
    "Souls": "Soul",
}

pouch_sizes = {
    25: "Miniature",
    50: "Cramped",
    100: "Small",
    250: "Average",
    500: "Sizable",
    1000: "Big",
    2000: "Large",
}

# --- Star signs ------------------------------------------------------------
# Hardcoded id -> index mapping (kept verbatim; the canonical source).
starsign_ids = {
    "The_Book_Worm": "1",
    "The_Buff_Guy": "1a",
    "The_Fuzzy_Dice": "1b",
    "Flexo_Bendo": "2",
    "Dwarfo_Beardus": "3",
    "Hipster_Logger": "4",
    "Pie_Seas": "4a",
    "Miniature_Game": "4b",
    "Shoe_Fly": "4c",
    "Pack_Mule": "5",
    "Pirate_Booty": "6",
    "All_Rounder": "7",
    "Muscle_Man": "7a",
    "Fast_Frog": "7b",
    "Smart_Stooge": "7c",
    "Lucky_Larry": "7d",
    "Fatty_Doodoo": "8",
    "Robinhood": "9",
    "Blue_Hedgehog": "9a",
    "Ned_Kelly": "10",
    "The_Fallen_Titan": "10a",
    "Chronus_Cosmos": "CR",
    "Activelius": "11",
    "Gum_Drop": "11a",
    "Mount_Eaterest": "12",
    "Bob_Build_Guy": "13",
    "The_Big_Comatose": "14",
    "Sir_Savvy": "14a",
    "Silly_Snoozer": "15",
    "The_Big_Brain": "15a",
    "Grim_Reaper": "16",
    "The_Forsaken": "16a",
    "The_OG_Skiller": "17",
    "Mr_No_Sleep": "18",
    "All_Rounderi": "19",
    "Centaurii": "20",
    "Murmollio": "21",
    "Strandissi": "22",
    "Agitagi": "22a",
    "Wispommo": "23",
    "Lukiris": "23a",
    "Pokaminini": "24",
    "Gor_Bowzor": "25",
    "Hydron_Cosmos": "26",
    "Trapezoidburg": "26a",
    "Sawsaw_Salala": "27",
    "Preys_Bea": "27B",
    "Cullingo": "28",
    "Gum_Drop_Major": "28a",
    "Grim_Reaper_Major": "29",
    "Sir_Savvy_Major": "30",
    "The_Bulwark": "31",
    "Big_Brain_Major": "32",
    "The_Fiesty": "33",
    "The_Overachiever": "33a",
    "Comatose_Major": "34",
    "S._Snoozer_Major": "35",
}

# `starsign_names` is the ordered list of star-sign *names* used to translate a
# star-sign index back into an id (see `get_starsign_from_index`). When the
# optional wiki StarSigns data is missing we fall back to the hardcoded ids
# (in declaration order) so parsing still works.
# R1(b): wiki StarSigns x authoritative starsign_ids 的安全合并。
# `starsign_names` 被 get_starsign_from_index 以【位置】索引
# (starsign_ids[starsign_names[i]]，见 core/parsers.py:70)，所以前
# len(starsign_ids) 项必须保持 starsign_ids 的声明顺序，且列表绝不缩短，
# 否则星座 index->id 会错位/越界。ARB wiki 列表更长(94)但顺序不同
# (ARB[0]=The_Buff_Guy 而 starsign_ids[0]=The_Book_Worm)，整体替换会破坏映射，
# 因此：
#   1) 按 starsign_ids 权威顺序填充（wiki 有则富化 name，无则回退硬编码 id）；
#   2) 将 wiki-only 项（W7 Cosmos 被动星座、Major/Minor 变体）追加到末尾。
_starsign_list = wiki_data.get("StarSigns", [])
_wiki_signs: dict = {}
if isinstance(_starsign_list, list):
    for _sign in _starsign_list:
        if isinstance(_sign, dict) and "name" in _sign:
            _wiki_signs[_sign["name"].replace(" ", "_")] = _sign
else:
    _starsign_list = []

_merged: list = []
_seen: set = set()
for _sid in starsign_ids:  # 权威顺序 -> 保持 index 映射不变
    _name = _wiki_signs.get(_sid, {}).get("name", _sid.replace("_", " "))
    _norm = _name.replace(" ", "_")
    _merged.append(_norm)
    _seen.add(_norm)
# 追加 wiki-only 星座（Cosmos 被动、变体）到末尾，既有 index 不受影响
for _name in _wiki_signs:
    if _name not in _seen:
        _merged.append(_name)
        _seen.add(_name)

if not _wiki_signs:
    logger.warning(
        "No StarSigns wiki data; deriving starsign_names from starsign_ids"
    )
starsign_names = _merged

constellation_names = [
    f"{c}-{i}" for c in "ABCD" for i in range(1, 13)
]

# --- Cogstruction maps (hardcoded) -----------------------------------------
cog_datas_map = {
    "a": "build_rate",
    "c": "flaggy_rate",
    "d": "exp_mult",
    "b": "exp_rate",
    "e": "build_rate_boost",
    "g": "flaggy_rate_boost",
    "k": "flaggy_speed",
    "f": "exp_rate_boost",
}

cog_boosts = "defg"

cog_type_map = {
    "ad": "Plus",
    "di": "X",
    "up": "Up",  # guess
    "do": "Down",
    "ri": "Right",
    "le": "Left",
    "ro": "Row",  # guess
    "co": "Col",  # guess
}
