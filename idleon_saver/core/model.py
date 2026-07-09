"""Source-agnostic intermediate model for idleon-saver.

The two source adapters (:class:`LocalExporter` / :class:`FirebaseExporter`)
*produce* an :class:`Account`, and every exporter *consumes* it. Keeping this
normalized model stable means exporters do not need to know whether the data
came from a local decode or a firebase cloud save.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from idleon_saver.utility import Sources


@dataclass
class Character:
    """Per-character view derived from an :class:`Account`."""

    name: str = ""
    klass: int = 0
    stats: List[int] = field(default_factory=list)
    starsign_equipped: str = ""
    skill_levels: List[int] = field(default_factory=list)
    bags_used: List[str] = field(default_factory=list)
    carrycaps: Dict[str, int] = field(default_factory=dict)
    statue_levels: List[Any] = field(default_factory=list)


@dataclass
class Account:
    """Normalized, source-agnostic representation of a player's save.

    Every field is the exact output a source adapter extracts from the raw
    save data. Exporters read these instead of poking at the raw save.
    """

    source: Sources = Sources.LOCAL
    names: List[str] = field(default_factory=list)
    classes: List[int] = field(default_factory=list)
    stats: List[List[int]] = field(default_factory=list)
    statue_levels: List[Any] = field(default_factory=list)
    bags_used: List[List[str]] = field(default_factory=list)
    carrycaps: List[Dict[str, int]] = field(default_factory=dict)
    starsigns_unlocked: Dict[str, int] = field(default_factory=dict)
    starsigns_prog: List[Any] = field(default_factory=list)
    starsigns_equipped: List[str] = field(default_factory=list)
    cauldron: List[Any] = field(default_factory=list)
    cards: Dict[str, int] = field(default_factory=dict)
    stamp_levels: List[List[int]] = field(default_factory=list)
    statues_golden: List[int] = field(default_factory=list)
    cog_map: List[Dict[str, Any]] = field(default_factory=list)
    cog_order: List[str] = field(default_factory=list)
    inv_storage_used: Dict[str, Any] = field(default_factory=dict)
    characters: List[Character] = field(default_factory=list)
