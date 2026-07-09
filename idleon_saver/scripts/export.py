"""Backward-compatible shim for ``idleon_saver.scripts.export``.

The implementation has moved into :mod:`idleon_saver.exporters`. This module
re-exports the public names so that existing import paths keep working:

* ``from idleon_saver.scripts.export import Exporter, exporters``
* ``from idleon_saver.scripts.export import get_cog_type, get_empties,
  get_starsign_from_index, parse_player_starsigns``
* ``idleon_saver.scripts.export.Formats`` (used by ``gui/main.kv``)
"""

import json
from argparse import Namespace
from pathlib import Path

from idleon_saver.exporters import (
    Exporter,
    FirebaseExporter,
    LocalExporter,
    exporters,
    get_cog_type,
    get_empties,
    get_starsign_from_index,
    parse_player_starsigns,
)
from idleon_saver.utility import Formats

__all__ = [
    "Exporter",
    "LocalExporter",
    "FirebaseExporter",
    "exporters",
    "get_cog_type",
    "get_empties",
    "get_starsign_from_index",
    "parse_player_starsigns",
    "Formats",
]


def main(args: Namespace):
    """Run the export for IC/COG formats (legacy entry point).

    Args:
        args: Namespace with ``workdir``, ``infile``, ``outfile``, ``source``
            and ``to`` attributes (per :mod:`idleon_saver.utility` arg adders).
    """
    infile = args.workdir / (args.infile or "decoded.json")
    with open(infile, encoding="utf-8") as file:
        data = json.load(file)
    exporters[args.source](data).export(args.to, args.workdir)


if __name__ == "__main__":
    from idleon_saver.cli import main as cli_main

    cli_main()
