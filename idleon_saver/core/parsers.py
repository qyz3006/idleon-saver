"""Pure parser functions for idleon-saver game systems.

These are the defensive, source-agnostic helpers the exporters rely on. They
were previously defined inline in ``idleon_saver.scripts.export``; they now
live here (per the rewrite design) and are re-exported by the
``idleon_saver.scripts.export`` shim for backward compatibility.
"""

import logging
from typing import Any, Dict, List, Optional

from idleon_saver.data import (
    card_reqs,
    cog_boosts,
    cog_datas_map,
    cog_type_map,
    idleon_data,
    pouch_names,
    pouch_sizes,
    starsign_ids,
    starsign_names,
)
from idleon_saver.utility import Sources

logger = logging.getLogger(__name__)


def get_baseclass(which: int) -> int:
    """Return the base class index for the given class *which*.

    Args:
        which: Class index from the save data.

    Returns:
        The base class index (warrior/archer/mage/beginner).

    Raises:
        ValueError: If *which* does not correspond to a known class.
    """
    if which in range(1, 5 + 1):
        # special case for beginner because it has only 4 subclasses
        return 1

    for base in [7, 19, 31]:  # warrior, archer, mage
        # each has 6 subclasses (unreleased but still in ClassNames)
        if which in range(base, base + 6 + 1):
            return base

    raise ValueError(f"Class {which} does not exist")


def get_classname(which: int) -> str:
    """Return a human-readable class name for class index *which*.

    Falls back to a generic label instead of crashing on unknown/future
    classes (e.g. classes the data submodule hasn't been updated for yet).
    """
    return idleon_data.get("classNames", {}).get(str(which), f"Class {which}")


def get_starsign_from_index(i: int) -> str:
    """Return the star-sign id for a 0-based star-sign *i*.

    Args:
        i: 0-based star-sign index.

    Returns:
        The corresponding star-sign id from :data:`starsign_ids`.
    """
    # 追加的 wiki-only 星座（如 Seraph_Cosmos）不在 starsign_ids 中，
    # 用 .get 回退到自身，避免 KeyError 被 parse_player_starsigns 丢弃。
    return starsign_ids.get(starsign_names[i], starsign_names[i])


def parse_player_starsigns(starsign_codes: str) -> Dict[str, bool]:
    """Parse a player's ``StarSign`` code string into an id->True mapping.

    Args:
        starsign_codes: Comma-separated star-sign indices, possibly with
            leading/trailing junk (``",,_"``).

    Returns:
        Mapping of star-sign id -> ``True`` for every valid index found.
    """
    starsigns: List[int] = []
    for k in starsign_codes.strip(",_").split(","):
        try:
            starsigns.append(get_starsign_from_index(int(k)))
        except ValueError:
            pass  # Malformed key ("" or "_")
        except KeyError as e:
            logger.exception(f"Couldn't parse starsign index {k}", exc_info=e)
    return dict.fromkeys(starsigns, True)


def get_cardtier(name: str, level: int) -> int:
    """Return the card tier (0-4) for *name* at *level*.

    Unknown cards (data submodule not yet updated for a new world) -> tier 0.

    Args:
        name: Internal card name.
        level: Card level from the save.

    Returns:
        Tier integer in ``0..4``.
    """
    req = card_reqs.get(name)
    if req is None:
        return 0
    if level == 0:
        return 0
    elif level >= req * 9:
        return 4
    elif level >= req * 4:
        return 3
    elif level >= req:
        return 2
    else:
        return 1


def get_pouchsize(itemtype: str, stacksize: int) -> str:
    """Return a friendly pouch-size word for *itemtype*/*stacksize*.

    Args:
        itemtype: Carry-cap material internal name.
        stacksize: Carry-cap stack size.

    Returns:
        A friendly size word, or ``"Unknown"`` for unrecognized sizes.
    """
    if stacksize == 25 and itemtype == "bCraft":
        return "Mini"
    if stacksize == 25 and itemtype == "Foods":
        return "Miniscule"
    # Fall back to a generic label instead of crashing if a new world (e.g. W7)
    # introduces a carry-cap stack size we don't have a friendly word for yet.
    return pouch_sizes.get(stacksize, "Unknown")


def get_pouches(carrycaps: Dict[str, int]) -> Dict[str, bool]:
    """Convert a carry-cap mapping into a pouch-name -> True mapping.

    Args:
        carrycaps: ``{material internal name: stack size}``.

    Returns:
        Mapping of ``"<size> <name> Pouch"`` -> ``True`` for every material with
        a stack size above the minimum threshold.
    """
    pouches: Dict[str, bool] = {}
    for itemtype, stacksize in carrycaps.items():
        if stacksize <= 10:
            continue
        # A new world (e.g. W7 Spelunking/Research) may add carry-cap material
        # types that aren't in the hardcoded `pouch_names` yet. Use the raw
        # internal name as a fallback so the export doesn't crash, and warn the
        # maintainer that the data submodule likely needs updating.
        if itemtype not in pouch_names:
            logger.warning(
                "Unknown carry-cap material type '%s' (W7 or later?) -- "
                "using raw name in pouch label; update data/__init__.py",
                itemtype,
            )
        size = get_pouchsize(itemtype, stacksize)
        name = pouch_names.get(itemtype, itemtype)
        pouches[" ".join([size, name, "Pouch"])] = True
    return pouches


def get_empties(cogs: List[str]) -> List[Dict[str, int]]:
    """Return the list of empty cog-board slots for *cogs*.

    The cog board is 8 rows by 12 columns = 96 spaces.

    Args:
        cogs: Ordered list of cog names (length >= 96).

    Returns:
        List of ``{"empties_x": x, "empties_y": y}`` dicts, one per blank slot.

    Raises:
        ValueError: If *cogs* has fewer than 96 entries.
    """
    if len(cogs) < 96:
        raise ValueError(
            "cog list must contain at least 96 entries to cover the whole cog "
            f"board; {len(cogs)} isn't enough"
        )

    empties: List[Dict[str, int]] = []
    for y in range(8):
        for x in range(12):
            i = y * 12 + x
            # Ignore occupied spaces.
            if cogs[i] == "Blank":
                empties.append({"empties_x": x, "empties_y": y})

    return empties


def get_cog_type(name: str) -> Optional[str]:
    """Return the cog type for a cog *name*, or ``None`` for blanks.

    Args:
        name: Cog name from the save data.

    Returns:
        A cog-type string (e.g. ``"Cog"``, ``"Character"``, ``"Yang_Cog"``),
        or ``None`` for ``"Blank"``.
    """
    # Check simple special cases.
    if name == "Blank":
        return None
    elif name.startswith("Player_"):
        return "Character"
    elif name == "CogY":
        return "Yang_Cog"
    elif name.startswith("CogZ"):
        return "Omni_Cog"

    # Check each type of directional cog.
    for direction, cog_type in cog_type_map.items():
        if name.endswith(direction):
            return f"{cog_type}_Cog"

    # If the name didn't match any special cases, it's just a regular cog.
    return "Cog"


def get_cog_data(cog: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    """Build the CSV row data for a single cog.

    Args:
        cog: The cog's bonus dict (may be missing keys).
        name: Cog name.

    Returns:
        A dict of cog-type + bonus fields, or ``None`` for a blank cog.
    """
    data: Dict[str, Any] = {}

    cog_type = get_cog_type(name)
    if cog_type is None:
        return None

    data["cog type"] = cog_type
    data["name"] = name.removeprefix("Player_") if cog_type == "Character" else ""

    for key, field in cog_datas_map.items():
        try:
            # Boosts are stored as percentages, so convert them to multipliers.
            data[field] = cog[key] / 100 if key in cog_boosts else cog[key]
        except KeyError:
            # Cogs only have keys for whatever bonuses they have,
            # but we need to fill in the missing fields for DictWriter.
            data[field] = ""

    return data


def detect_source(savedata: Dict[str, Any]) -> Sources:
    """Heuristically detect whether *savedata* is local or firebase.

    Args:
        savedata: A decoded (local) or raw (firebase) save dict.

    Returns:
        :attr:`Sources.FIREBASE` or :attr:`Sources.LOCAL`.
    """
    if not isinstance(savedata, dict):
        return Sources.LOCAL
    # Firebase saves store player names flat and cards under Cards0.
    if "PlayerNames" in savedata or "Cards0" in savedata:
        return Sources.FIREBASE
    # Local (decoded) saves keep names under GetPlayersUsernames and nest
    # players inside PlayerDATABASE.
    if "GetPlayersUsernames" in savedata or "PlayerDATABASE" in savedata:
        return Sources.LOCAL
    # Default to the local (decoded JSON) shape.
    return Sources.LOCAL
