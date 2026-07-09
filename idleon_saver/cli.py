"""Unified command-line interface for idleon-saver.

This is the single ``console_scripts`` entry point (``idleon-saver``). It is
the sole owner of argument parsing; the pipeline modules under
:mod:`idleon_saver.scripts` keep their ``main(args)`` workers and delegate
parsing here.

Subcommands::

    idleon-saver decode   [--source local|firebase] [--out encoded.txt] ...
    idleon-saver encode   [--in decoded_types.json] [--out encoded.txt] ...
    idleon-saver export   [--format companion|cogstruction|toolbox|efficiency] ...
    idleon-saver gui      # launch the Kivy desktop app
"""

import argparse
import json
import logging
import sys
from argparse import Namespace
from pathlib import Path
from typing import List, Optional

from idleon_saver.log import configure_logging
from idleon_saver.utility import Args, Formats, Sources, arg_adders

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="idleon-saver",
        description="Convert Legends of Idleon save data to/from JSON and "
        "export it to community tools.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_decode = sub.add_parser(
        "decode", help="Decode a LevelDB save to a Stencyl/JSON blob."
    )
    for arg in (Args.IDLEON, Args.LDB, Args.WORKDIR, Args.OUTFILE, Args.SOURCE):
        arg_adders[arg](p_decode)

    p_encode = sub.add_parser(
        "encode", help="Encode a JSON blob back into a Stencyl/LevelDB save."
    )
    for arg in (Args.LDB, Args.IDLEON, Args.WORKDIR, Args.INFILE, Args.OUTFILE):
        arg_adders[arg](p_encode)

    p_export = sub.add_parser(
        "export", help="Export a decoded save to a community format."
    )
    for arg in (Args.WORKDIR, Args.INFILE, Args.OUTFILE, Args.SOURCE, Args.TO):
        arg_adders[arg](p_export)

    sub.add_parser("gui", help="Launch the Kivy desktop GUI.")
    return parser


def _coerce_source(args: Namespace) -> Sources:
    """Return *args.source* as a :class:`Sources` enum."""
    return args.source if isinstance(args.source, Sources) else Sources(args.source)


def _coerce_format(args: Namespace) -> Formats:
    """Return *args.to* as a :class:`Formats` enum."""
    return args.to if isinstance(args.to, Formats) else Formats(args.to)


def run_decode(args: Namespace) -> None:
    """Run the decode pipeline (ldb -> stencyl -> json)."""
    from idleon_saver.scripts.decode import main as decode_main

    decode_main(args)


def run_encode(args: Namespace) -> None:
    """Run the encode pipeline (json -> stencyl -> ldb)."""
    from idleon_saver.scripts.encode import main as encode_main

    encode_main(args)


def run_export(args: Namespace) -> None:
    """Run an export of *args.to* format."""
    from idleon_saver.exporters import (
        CogstructionExporter,
        EfficiencyExporter,
        IdleonCompanionExporter,
        ToolboxExporter,
    )

    infile = args.workdir / (args.infile or "decoded.json")
    with open(infile, encoding="utf-8") as file:
        savedata = json.load(file)

    source = _coerce_source(args)
    fmt = _coerce_format(args)
    outdir = args.workdir / (args.outfile or fmt.value)

    if fmt == Formats.IC:
        IdleonCompanionExporter().export(savedata, source, outdir)
    elif fmt == Formats.COG:
        CogstructionExporter().export(savedata, source, outdir)
    elif fmt == Formats.TOOLBOX:
        ToolboxExporter().export(savedata, source, outdir)
    elif fmt == Formats.EFFICIENCY:
        EfficiencyExporter().export(savedata, source, outdir)
    else:  # pragma: no cover - argparse restricts choices
        raise ValueError(f"Unknown export format: {fmt}")


def run_gui(args: Namespace) -> None:
    """Launch the Kivy GUI."""
    from idleon_saver.gui import main as gui_main

    gui_main.main()


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the ``idleon-saver`` console script.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    """
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command == "decode":
        run_decode(args)
    elif command == "encode":
        run_encode(args)
    elif command == "export":
        run_export(args)
    elif command == "gui":
        run_gui(args)
    else:  # pragma: no cover - argparse enforces a subcommand
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
