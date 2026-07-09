"""IdleonCompanion exporter.

Wraps the existing :meth:`Exporter.to_idleon_companion` logic and writes the
result to ``idleon_companion.json``.
"""

import json
import logging
from pathlib import Path
from typing import Union

from idleon_saver.exporters.base import exporters
from idleon_saver.utility import Sources

logger = logging.getLogger(__name__)


class IdleonCompanionExporter:
    """Export a decoded/firebase save to IdleonCompanion JSON."""

    def export(self, savedata: dict, source: Sources, outdir: Union[str, Path]) -> Path:
        """Write ``idleon_companion.json`` into *outdir*.

        Args:
            savedata: Raw decoded or firebase save dict.
            source: :attr:`Sources.LOCAL` or :attr:`Sources.FIREBASE`.
            outdir: Directory to write the output file into.

        Returns:
            Path to the written ``idleon_companion.json``.
        """
        exporter = exporters[source](savedata)
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        outfile = outdir / "idleon_companion.json"
        with open(outfile, "w", encoding="utf-8") as file:
            json.dump(exporter.to_idleon_companion(), file)
        logger.info("Wrote file: %s", outfile)
        return outfile
