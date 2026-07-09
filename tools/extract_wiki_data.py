"""Build-time extractor for vendored wiki static data.

Reads the IdleOnAutoReviewBot (ARB) ``consts/*.py`` source via the
standard-library :mod:`ast` module ONLY. It never imports ARB (which would pull
in django and other heavy dependencies) and instead statically extracts the
literal values of a few top-level assignments. It then writes:

    StarSigns.json     <- consts/consts_w1.py::StarSigns + passive_starsigns
    EnemyDetails.json  <- consts/generated/monster_data.py::monster_data

The output files are consumed by ``idleon_saver.data`` (which loads every
``vendored/wiki/**/*.json`` into ``wiki_data``) and by the exporters
(``get_cards`` reads ``wiki_data["EnemyDetails"]``).

Run from the idleon-saver repo root::

    python tools/extract_wiki_data.py \
        --arb "E:/Downloads/IdleOnAutoReviewBot-main.zip" \
        --out idleon_saver/data/vendored/wiki

``--arb`` may be a ``.zip`` (auto-extracted to a temp dir) or an already
extracted directory. ``--out`` is created if it does not exist.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_py_assignments(path: Path, name: str) -> Any:
    """Return the literal value of a top-level ``name = ...`` in *path*.

    Uses :mod:`ast` parsing only (the ARB package is never imported). Raises
    :class:`KeyError` if the assignment is not present at module top level.

    Args:
        path: Path to the ARB ``.py`` source file.
        name: Target variable name to extract.

    Returns:
        The value produced by :func:`ast.literal_eval` on the assignment's
        right-hand side (a plain Python literal).

    Raises:
        KeyError: If no top-level assignment to *name* exists.
        ValueError: If the value is not a literal ``ast.literal_eval`` can read.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError(f"Top-level assignment {name!r} not found in {path}")


def _resolve_consts_root(arb: Path) -> Path:
    """Return the ARB ``mysite/consts`` directory.

    If *arb* is a ``.zip`` it is extracted to a temporary directory first. If it
    is a directory it is used directly (searching for ``consts_w1.py`` so either
    the repo root, ``mysite``, or ``mysite/consts`` is acceptable).

    Args:
        arb: Path to the ARB zip or extracted directory.

    Returns:
        The directory that contains ``consts_w1.py``.

    Raises:
        FileNotFoundError: If *arb* is neither a zip nor a directory, or the
            consts directory cannot be located.
    """
    if arb.is_file() and arb.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="arb_extract_"))
        logger.info("Extracting %s -> %s", arb, tmp)
        with zipfile.ZipFile(arb) as zf:
            zf.extractall(tmp)
        search_root = tmp
    elif arb.is_dir():
        search_root = arb
    else:
        raise FileNotFoundError(
            f"--arb path is neither a zip nor a directory: {arb}"
        )

    matches = list(search_root.rglob("consts_w1.py"))
    if not matches:
        raise FileNotFoundError(
            f"Could not locate consts_w1.py under {search_root}"
        )
    return matches[0].parent


def extract_starsigns(consts_root: Path) -> list:
    """Build the ``StarSigns.json`` payload from ARB ``consts_w1.py``.

    ``StarSigns`` is a list of ``[name, bonus1, bonus2, bonus3]`` rows; we keep
    the readable (space-separated) ``name`` and the first three bonuses.
    ``passive_starsigns`` is a list of star-sign name strings (e.g.
    ``Chronus_Cosmos``); each becomes ``{"name": "Chronus Cosmos", "bonuses": []}``
    and is appended. Duplicate passive names already present in ``StarSigns`` are
    skipped so their (richer) bonuses are preserved.

    Args:
        consts_root: The ARB ``mysite/consts`` directory.

    Returns:
        A list of ``{"name": str, "bonuses": [str, ...]}`` dicts.
    """
    w1 = consts_root / "consts_w1.py"
    rows = _load_py_assignments(w1, "StarSigns")
    passive = _load_py_assignments(w1, "passive_starsigns")

    result: list = []
    seen: set = set()
    # StarSigns: list-of-lists [name, b1, b2, b3] -> {name, bonuses}.
    for row in rows:
        if not (isinstance(row, (list, tuple)) and row):
            continue
        name = str(row[0]).replace("_", " ")
        norm = name.replace(" ", "_")
        if norm in seen:
            continue
        bonuses = [str(b) for b in row[1:4]]
        result.append({"name": name, "bonuses": bonuses})
        seen.add(norm)
    # passive_starsigns: list-of-str -> {name} (skip if already present so the
    # StarSigns bonuses are preserved for those signs).
    for p in passive:
        norm = str(p).replace("_", " ")
        key = norm.replace(" ", "_")
        if key in seen:
            continue
        result.append({"name": norm, "bonuses": []})
        seen.add(key)
    return result


def extract_enemy_details(consts_root: Path) -> dict:
    """Build the ``EnemyDetails.json`` payload 1:1 from ARB ``monster_data``.

    ARB ``monster_data`` is already ``{code: {"Name": display}}``. We emit it
    directly, dropping the non-monster metadata key (``_hash``) and any entry
    that is not a ``{"Name": ...}`` dict, so the consumer ``get_cards`` never
    crashes on ``enemy["Name"]``.

    Args:
        consts_root: The ARB ``mysite/consts`` directory.

    Returns:
        A dict mapping internal monster/card codes to ``{"Name": str}``.
    """
    monster_file = consts_root / "generated" / "monster_data.py"
    monster_data = _load_py_assignments(monster_file, "monster_data")

    result: dict = {}
    for code, value in monster_data.items():
        if not (isinstance(value, dict) and "Name" in value):
            # Skip the metadata ``_hash`` key and any malformed entry.
            continue
        result[code] = {"Name": str(value["Name"])}
    return result


def main() -> None:
    """CLI entry point: parse args, extract, and write the vendored JSON."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--arb",
        required=True,
        type=Path,
        help="Path to the IdleOnAutoReviewBot zip or extracted directory.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory for the generated vendored wiki JSON files.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    consts_root = _resolve_consts_root(args.arb)
    logger.info("Using ARB consts root: %s", consts_root)

    args.out.mkdir(parents=True, exist_ok=True)

    starsigns = extract_starsigns(consts_root)
    # NOTE: ``_load_json_files`` (idleon_saver/data/__init__.py) calls
    # ``jsondata.get("data", jsondata)`` on every file, so a bare top-level list
    # would crash at import. We therefore wrap the list in the same
    # ``{"data": [...]}`` envelope the existing vendored maps use; the loader
    # unwraps it back to the list. (EnemyDetails is already a dict, so it needs
    # no envelope.)
    (args.out / "StarSigns.json").write_text(
        json.dumps({"data": starsigns}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote StarSigns.json (%d entries)", len(starsigns))

    enemy_details = extract_enemy_details(consts_root)
    (args.out / "EnemyDetails.json").write_text(
        json.dumps(enemy_details, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Wrote EnemyDetails.json (%d entries)", len(enemy_details))


if __name__ == "__main__":
    main()
