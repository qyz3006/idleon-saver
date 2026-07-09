"""Core exporter classes for idleon-saver.

This is the ported ``Exporter`` ABC plus its two source adapters
(:class:`LocalExporter` / :class:`FirebaseExporter`). Each adapter *builds* a
normalized :class:`~idleon_saver.core.model.Account` and copies the needed
fields onto itself as **plain, writable instance attributes** (``cauldron``,
``stats``, ``cog_order``, ``cog_map``, ``stamp_levels``, ``statues_golden``,
``starsigns_*``, ``cards``, ``classes``, ``names``, ``bags_used``,
``carrycaps``, ``inv_storage_used``) so the existing test suite -- which pokes
``exporter.cauldron = []`` / ``exporter.stats = [[]]`` -- keeps working.

The pure parser helpers live in :mod:`idleon_saver.core.parsers` and are
re-imported here.
"""

import csv
import json
import logging
from abc import ABC, abstractmethod
from argparse import Namespace
from itertools import chain, repeat, starmap
from math import floor
from pathlib import Path
from string import ascii_lowercase
from typing import Any, Dict, Iterator, List, Optional, Tuple

from idleon_saver.core.converters import safe_get
from idleon_saver.core.model import Account, Character
from idleon_saver.core.parsers import (
    get_cog_data,
    get_classname,
    get_pouches,
    parse_player_starsigns,
)
from idleon_saver.data import (
    Bags,
    bag_maps,
    card_reqs,
    cog_boosts,
    cog_datas_map,
    cog_type_map,
    constellation_names,
    idleon_data,
    pouch_names,
    pouch_sizes,
    skill_names,
    stamp_names,
    starsign_ids,
    starsign_names,
    statues,
    vial_names,
    wiki_data,
)
from idleon_saver.utility import (
    Sources,
    friendly_name,
    from_keys_in,
    zip_from_iterable,
)

logger = logging.getLogger(__name__)


class Exporter(ABC):
    """Abstract base for all exporters.

    Subclasses implement :meth:`all_players` and set the abstract instance
    attributes (``names``, ``stats``, ``cauldron``, ...) in their
    ``__init__``. The base class provides every format-specific builder.
    """

    def __init__(self, savedata: dict) -> None:
        self.savedata = savedata

        self.classes: List[int] = self.all_players("CharacterClass")
        self.skill_levels: List[List[int]] = self.all_players("Lv0")
        self.statue_levels: List[Any] = self.all_players("StatueLevels")
        self.bags_used: List[List[str]] = [
            bags.keys() for bags in self.all_players("InvBagsUsed")
        ]  # Values are just the number of slots granted by the bag, so ignore them
        self.carrycaps: List[Dict[str, int]] = self.all_players("MaxCarryCap")

        # Abstract attributes defined by subclasses
        self.names: List[str]
        self.stats: List[List[int]]
        self.cauldron: List[Any]
        self.starsigns_unlocked: Dict[str, int]
        self.starsigns_prog: List[Any]
        self.starsigns_equipped: List[str]
        self.cards: Dict[str, int]
        self.stamp_levels: List[List[int]]
        self.statues_golden: List[int]
        self.cog_map: List[Dict[str, Any]]
        self.cog_order: List[str]

    @abstractmethod
    def all_players(self, key: str) -> List[Any]:
        """Return the value stored under *key* for every player."""

    def export(self, fmt: Any, workdir: Path):
        """Export to the given :class:`Formats` into *workdir*.

        Args:
            fmt: A :class:`~idleon_saver.utility.Formats` member.
            workdir: Directory to write the output files into.
        """
        from idleon_saver.utility import Formats

        if fmt == Formats.IC:
            self.save_idleon_companion(workdir)
        elif fmt == Formats.COG:
            self.save_cogstruction(workdir)
        else:
            raise ValueError(
                f"Format must be idleon_companion or cogstruction, not {fmt}"
            )

    # --- IdleonCompanion ---------------------------------------------------
    def save_idleon_companion(self, workdir: Path):
        outfile = workdir / "idleon_companion.json"

        with open(outfile, "w", encoding="utf-8") as file:
            json.dump(self.to_idleon_companion(), file)

        logger.info(f"Wrote file: {outfile}")

    def to_idleon_companion(self) -> dict:
        return {
            "alchemy": self.get_alchemy(),
            "starSigns": self.get_starsigns(),
            "cards": self.get_cards(),
            "stamps": {name: level for name, level in self.get_stamps() if level > 0},
            "statues": self.get_statues(),
            "checklist": self.get_checklist(),
            "chars": self.get_chars(),
        }

    # --- Cogstruction ------------------------------------------------------
    def save_cogstruction(self, workdir: Path):
        data = self.to_cogstruction()

        for which in ["cog_datas", "empties_datas"]:
            outfile = workdir / f"{which}.csv"

            with open(outfile, "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=data[which][0].keys())

                writer.writeheader()
                for row in data[which]:
                    writer.writerow(row)

            logger.info(f"Wrote file: {outfile}")

    def to_cogstruction(self) -> Dict[str, Any]:
        return {
            "cog_datas": list(
                filter(
                    None,
                    starmap(
                        get_cog_data,
                        zip(self.cog_map, self.cog_order),
                    ),
                )
            ),  # Filter to ignore blank cogs.
            "empties_datas": self.get_empties(),
        }

    # --- Shared builders ---------------------------------------------------
    def char_map(self) -> Dict[str, str]:
        return dict(zip(self.names, "_" + ascii_lowercase))

    def get_alchemy(self) -> dict:
        # Get possibly empty alchemy data.
        try:
            upgrades = self.cauldron[:4]
        except IndexError:
            upgrades = repeat([])
        try:
            vial_levels = self.cauldron[4]
        except IndexError:
            vial_levels = []

        # If the save has more vials than our hardcoded name list, the extras
        # are silently dropped by zip() -- flag it so the data submodule can be
        # updated for a new world (e.g. W7 may add vials).
        if len(vial_levels) > len(vial_names):
            logger.warning(
                "Save has %d vials but only %d vial names known; newer vials "
                "may be missing from the data submodule (W7+).",
                len(vial_levels),
                len(vial_names),
            )

        return {
            "upgrades": dict(
                zip(("Orange", "Green", "Purple", "Yellow"), upgrades)
            ),
            "vials": {
                friendly_name(name): level
                for name, level in zip(vial_names, vial_levels)
                if level > 0
            },
        }

    def get_starsigns(self) -> Dict[str, bool]:
        return {
            # Fall back to the raw name if a future world adds a star sign
            # beyond the 48 we have hardcoded in data/__init__.py.
            starsign_ids.get(name, name): bool(unlocked)
            for name, unlocked in self.starsigns_unlocked.items()
        }

    def get_cards(self) -> Dict[str, int]:
        cards: Dict[str, int] = {}
        enemy_details = safe_get(wiki_data, "EnemyDetails", {})
        if not isinstance(enemy_details, dict):
            enemy_details = {}
        for name, level in self.cards.items():
            if level <= 0:
                continue
            enemy = enemy_details.get(name)
            if enemy is None:
                # W7 (or future) card not yet present in the data submodule.
                # Keep the raw internal name as the key instead of crashing.
                cards[name] = self._cardtier(name, level)
                continue
            cards[enemy["Name"]] = self._cardtier(name, level)
        return cards

    @staticmethod
    def _cardtier(name: str, level: int) -> int:
        """Compute a card tier defensively (mirrors ``get_cardtier``)."""
        from idleon_saver.core.parsers import get_cardtier

        return get_cardtier(name, level)

    def get_stamps(self) -> Iterator[Tuple[str, int]]:
        return chain.from_iterable(
            zip(stamps, levels)
            for stamps, levels in zip(stamp_names, self.stamp_levels)
        )

    def get_statues(self) -> dict:
        """Explanation of zip magic:
        0. statue_levels:
           list of characters, each with a list of statues, each with (level, progress)
        1. zip_from_iterable(_):
           list of statues, each with a list of characters, each with (level, progress)
        2. map(zip_from_iterable, _):
           list of statues, each with (list of levels, list of progresses)
        3. zip_from_iterable(_):
           ([list of statues, each with a list of levels], [list of statues, each with a list of progresses])
        4. for lvls, progs in zip(*_):
           iterates over statues, unpacking each into a list of levels and list of progresses
        """
        # If the save has more statues than our name list, the extras are
        # silently dropped by zip() -- flag it (e.g. a new world added a statue).
        if len(self.statues_golden) != len(statues):
            logger.warning(
                "Save has %d statues but only %d statue names known; some "
                "statues may be missing from the data submodule (W7+).",
                len(self.statues_golden),
                len(statues),
            )

        return {
            name: {
                "golden": bool(gold),
                "level": max(lvls),
                "progress": floor(max(progs)),
            }
            for name, gold, lvls, progs in zip(
                statues,
                self.statues_golden,
                *zip_from_iterable(
                    map(zip_from_iterable, zip_from_iterable(self.statue_levels))
                ),
            )
        }

    def get_checklist(self) -> Dict[str, bool]:
        return (
            # DeepSource error due to old python/mypy version? skipcq: TYP-052
            from_keys_in(
                bag_maps[Bags.GEM],
                self.bags_used[0],
                True,
            )
            | from_keys_in(
                bag_maps[Bags.STORAGE],
                # Guard against the key being absent/renamed in some save
                # variants instead of crashing the whole export.
                self.savedata.get("InvStorageUsed", {}).keys(),
                True,
            )
            | {name: True for name, level in self.get_stamps() if level > 0}
        )

    def get_player_constellations(self, charname: str) -> Dict[str, bool]:
        result: Dict[str, bool] = {}
        for i, (chars, completed) in enumerate(self.starsigns_prog):
            # Guard against more constellations than our hardcoded 48 (A-D x 12),
            # which would happen if a future world adds star signs.
            if i >= len(constellation_names):
                logger.warning(
                    "Star sign progress index %d exceeds known constellations (%d)",
                    i,
                    len(constellation_names),
                )
                break
            if self.char_map()[charname] in (chars or ""):  # chars can be null
                result[constellation_names[i]] = True
        return result

    def get_empties(self) -> List[Dict[str, int]]:
        """Return the empty cog-board slots (delegates to parsers helper)."""
        from idleon_saver.core.parsers import get_empties

        return get_empties(self.cog_order)

    def build_skills(self, skills: List[int]) -> Dict[str, int]:
        # `Lv0` carries skill levels at indices 0..len(skill_names)-1. Indices
        # beyond that are NOT skills -- a W7 save's `Lv0` array has 25 entries but
        # only the first 21 are skills (the rest are other per-character data,
        # e.g. elite-class placeholders), so we must not label them. `zip()`
        # already truncates to the shorter list, so extras are ignored. We only
        # warn so a maintainer knows to extend `skill_names` when a new world
        # actually adds skills (W8+).
        if len(skills) > len(skill_names):
            logger.warning(
                "Character has %d Lv0 entries but only %d skills are known; "
                "trailing entries ignored (update data/__init__.py for new worlds).",
                len(skills),
                len(skill_names),
            )
        # Index 0 is "Character" (level handled separately via stats[4]); drop it.
        return dict(list(zip(skill_names, skills))[1:])

    def build_char(
        self,
        name: str,
        klass: int,
        stats: List[int],
        starsigns: str,
        skills: List[int],
        bags: List[str],
        carrycaps: Dict[str, int],
    ) -> dict:
        try:
            level = stats[4]
        except IndexError:
            # Characters that have never been played have placeholder stats
            # that don't include level, so make up a placeholder level.
            level = 0

        return {
            "name": name,
            "class": get_classname(klass),
            "level": level,
            "constellations": self.get_player_constellations(name),
            "starSigns": parse_player_starsigns(starsigns),
            "skills": self.build_skills(skills),
            # DeepSource error due to old python/mypy version? skipcq: TYP-052
            "items": from_keys_in(bag_maps[Bags.INV], bags, True)
            | get_pouches(carrycaps),
        }

    def get_chars(self) -> List[dict]:
        return list(
            starmap(
                self.build_char,
                zip(
                    self.names,
                    self.classes,
                    self.stats,
                    self.starsigns_equipped,
                    self.skill_levels,
                    self.bags_used,
                    self.carrycaps,
                ),
            )
        )

    # --- Account construction (design: adapters build an Account) ----------
    def build_account(self, inv_storage_used: Dict[str, Any]) -> Account:
        """Build the normalized :class:`Account` from this exporter's fields.

        Args:
            inv_storage_used: The raw ``InvStorageUsed`` mapping (if any).

        Returns:
            A populated :class:`~idleon_saver.core.model.Account`.
        """
        from idleon_saver.core.parsers import detect_source

        characters = [
            Character(
                name=name,
                klass=klass,
                stats=stats,
                starsign_equipped=starsign,
                skill_levels=skills,
                bags_used=bags,
                carrycaps=carrycaps,
                statue_levels=[],
            )
            for name, klass, stats, starsign, skills, bags, carrycaps in zip(
                self.names,
                self.classes,
                self.stats,
                self.starsigns_equipped,
                self.skill_levels,
                self.bags_used,
                self.carrycaps,
            )
        ]
        return Account(
            source=detect_source(self.savedata),
            names=self.names,
            classes=self.classes,
            stats=self.stats,
            statue_levels=self.statue_levels,
            bags_used=self.bags_used,
            carrycaps=self.carrycaps,
            starsigns_unlocked=self.starsigns_unlocked,
            starsigns_prog=self.starsigns_prog,
            starsigns_equipped=self.starsigns_equipped,
            cauldron=self.cauldron,
            cards=self.cards,
            stamp_levels=self.stamp_levels,
            statues_golden=self.statues_golden,
            cog_map=self.cog_map,
            cog_order=self.cog_order,
            inv_storage_used=inv_storage_used,
            characters=characters,
        )


class LocalExporter(Exporter):
    """Adapter for a locally-decoded save (the ``local.json`` shape)."""

    def __init__(self, savedata: dict) -> None:
        super().__init__(savedata)
        self.names = savedata["GetPlayersUsernames"]
        self.stats = [pv["StatList"] for pv in self.all_players("PersonalValuesMap")]
        self.starsigns_equipped = [
            pv["StarSign"] for pv in self.all_players("PersonalValuesMap")
        ]
        self.starsigns_unlocked = savedata["StarSignsUnlocked"]
        self.starsigns_prog = savedata["StarSignProg"]
        self.cauldron = savedata["CauldronInfo"]
        self.cards = savedata["Cards"][0]
        self.stamp_levels = savedata["StampLevel"]
        self.statues_golden = savedata["StatueG"]
        self.cog_map = savedata["CogMap"]
        self.cog_order = savedata["CogOrder"]
        self.account = self.build_account(savedata.get("InvStorageUsed", {}))

    def all_players(self, key: str) -> List[Any]:
        return [player[key] for player in self.savedata["PlayerDATABASE"].values()]


class FirebaseExporter(Exporter):
    """Adapter for a raw firebase cloud-save (the ``firebase.json`` shape)."""

    def __init__(self, savedata: dict) -> None:
        super().__init__(savedata)
        self.names = savedata["PlayerNames"]
        self.stats = self.all_players("PVStatList")
        self.starsigns_equipped = self.all_players("PVtStarSign")
        self.starsigns_unlocked = savedata["StarSg"]
        self.starsigns_prog = savedata["SSprog"]
        self.cauldron = list(map(self.parse_pseudoarray, savedata["CauldronInfo"]))
        self.cards = savedata["Cards0"]
        self.stamp_levels = list(map(self.parse_pseudoarray, savedata["StampLv"]))
        self.statues_golden = savedata["StuG"]
        self.cog_order = savedata["CogO"]
        self.cog_map = self.parse_cog_map(savedata["CogM"])
        self.account = self.build_account(savedata.get("InvStorageUsed", {}))

    @staticmethod
    def parse_pseudoarray(obj: dict) -> List[Any]:
        if "length" not in obj:
            raise ValueError(f"Object has no `length` key: {obj}")
        return [v for k, v in obj.items() if k != "length"]

    def all_players(self, key: str) -> List[Any]:
        return [v for k, v in sorted(self.savedata.items()) if k.startswith(key)]

    def parse_cog_map(self, cog_map: Dict[str, dict]) -> List[Dict[str, Any]]:
        new_cogs: List[Dict[str, Any]] = []
        for i in range(len(self.cog_order)):
            try:
                new_cogs.append(cog_map[str(i)])
            except KeyError:
                new_cogs.append({})
        return new_cogs


exporters = {
    Sources.LOCAL: LocalExporter,
    Sources.FIREBASE: FirebaseExporter,
}
