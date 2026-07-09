"""Defensive data-loading tests for idleon-saver.

These verify that :mod:`idleon_saver.data` loads the vendored maps, derives
all exported names without raising, and degrades gracefully when files or the
whole directory are missing. They are self-contained (no conftest fixtures)
so they can run with ``pytest tests/test_data.py --noconftest`` even when the
optional ``plyvel`` dependency is absent.
"""

import json
import logging
from pathlib import Path

from idleon_saver.data import (
    Bags,
    bag_maps,
    card_reqs,
    idleon_data,
    stamp_names,
    starsign_ids,
    starsign_names,
    statues,
    vial_names,
    wiki_data,
)


def test_idleon_data_loaded_from_vendored_maps():
    """The committed vendored maps are present and parsed."""
    assert isinstance(idleon_data, dict)
    for key in ("statueList", "cards", "alchemy", "bags", "stampList", "classNames"):
        assert key in idleon_data


def test_statues_nonempty_and_suffix():
    """statues must be non-empty and every entry ends with ' Statue'."""
    assert len(statues) > 0
    for statue in statues:
        assert statue.endswith(" Statue")


def test_card_reqs_derived():
    assert isinstance(card_reqs, dict)
    assert len(card_reqs) > 0


def test_vial_names_derived():
    assert isinstance(vial_names, list)
    assert len(vial_names) > 0
    assert all(isinstance(name, str) for name in vial_names)


def test_stamp_names_three_groups():
    assert len(stamp_names) == 3
    for group in stamp_names:
        assert isinstance(group, list)


def test_bag_maps_present():
    assert bag_maps[Bags.INV]
    assert bag_maps[Bags.STORAGE]
    # GEM has no vendored source -> safe empty placeholder (not crashing).
    assert bag_maps[Bags.GEM] == {}


def test_starsign_names_from_wiki_not_fallback():
    """Acceptance #3 (R1b): with vendored wiki StarSigns present, the derived
    ``starsign_names`` now comes from the wiki, not the hardcoded ids fallback.
    The first ``len(starsign_ids)`` entries must keep the authoritative
    ``starsign_ids`` declaration order so the index->id mapping never shifts."""
    ids_keys = list(starsign_ids.keys())
    # No longer the bare fallback list (this is the whole point of the fix).
    assert starsign_names != ids_keys
    # Authoritative order preserved at the front (R1b safe merge).
    assert starsign_names[: len(ids_keys)] == ids_keys
    # The list is never shorter than the authoritative count.
    assert len(starsign_names) >= len(ids_keys)


def test_wiki_data_loaded_from_vendored():
    """Acceptance #5 / R2: vendored/wiki is now loaded into ``wiki_data`` (no
    longer empty), and ``EnemyDetails`` is a non-empty mapping whose entries
    carry a display ``Name`` that ``get_cards`` consumes."""
    assert isinstance(wiki_data, dict)
    assert wiki_data  # directory is vendored -> not empty
    # StarSigns was loaded (as a list, via the ``data`` envelope).
    stars = wiki_data.get("StarSigns", [])
    assert isinstance(stars, list) and stars
    # EnemyDetails: non-empty dict, each entry has a "Name".
    enemy_details = wiki_data.get("EnemyDetails", {})
    assert isinstance(enemy_details, dict) and enemy_details
    assert "Bandit_Bob" in enemy_details
    assert enemy_details["Bandit_Bob"]["Name"] == "Bandit Bob"


def test_load_json_files_missing_dir_returns_empty_and_warns(tmp_path, caplog):
    from idleon_saver.data import _load_json_files

    missing = tmp_path / "does_not_exist"
    with caplog.at_level(logging.WARNING):
        result = _load_json_files(missing)
    assert result == {}
    assert any("missing" in record.message.lower() for record in caplog.records)


def test_load_json_files_recursive_missing_dir(tmp_path, caplog):
    from idleon_saver.data import _load_json_files

    missing = tmp_path / "nope"
    with caplog.at_level(logging.WARNING):
        result = _load_json_files(missing, recursive=True)
    assert result == {}


def test_load_json_files_reads_and_unwraps(tmp_path):
    from idleon_saver.data import _load_json_files

    d = tmp_path / "maps"
    d.mkdir()
    (d / "foo.json").write_text(
        json.dumps({"__comment": "ignored", "data": [1, 2, 3]})
    )
    result = _load_json_files(d)
    assert result["foo"] == [1, 2, 3]


def test_load_json_files_skips_malformed(tmp_path, caplog):
    from idleon_saver.data import _load_json_files

    d = tmp_path / "maps"
    d.mkdir()
    (d / "bad.json").write_text("{ this is not valid json")
    with caplog.at_level(logging.WARNING):
        result = _load_json_files(d)
    assert result == {}
    assert any("Could not read" in record.message for record in caplog.records)


def test_derivations_are_safe_containers():
    """Sanity: the exported derivation names are always the right container
    type, so downstream code can iterate them without guarding."""
    assert isinstance(statues, list)
    assert isinstance(card_reqs, dict)
    assert isinstance(vial_names, list)
    assert isinstance(stamp_names, list)
    assert isinstance(starsign_names, list)


def test_import_no_vendored_warnings(caplog):
    """Acceptance #1: importing idleon_saver.data must NOT emit the two
    wiki-degradation warnings now that vendored/wiki is populated. We reload the
    module inside a logging capture so the import-time warnings are observed."""
    import importlib

    import idleon_saver.data as data_module

    with caplog.at_level(logging.WARNING):
        importlib.reload(data_module)
    messages = [record.message for record in caplog.records]
    assert not any("Vendored data directory missing" in m for m in messages)
    assert not any("No StarSigns wiki data" in m for m in messages)


def test_get_starsign_from_index_wiki_only_no_keyerror():
    """Acceptance (R1b): star signs appended past the authoritative 57 (W7
    Cosmos passives, Major/Minor variants) must resolve via the ``.get``
    fallback instead of raising KeyError inside ``parse_player_starsigns``."""
    from idleon_saver.core.parsers import get_starsign_from_index

    wiki_only_index = len(starsign_ids)
    assert wiki_only_index < len(starsign_names)
    name = starsign_names[wiki_only_index]
    # Must not raise; appended signs fall back to their own (normalized) name.
    assert get_starsign_from_index(wiki_only_index) == name
