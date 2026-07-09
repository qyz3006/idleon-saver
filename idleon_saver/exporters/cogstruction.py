"""Cogstruction exporter.

Wraps the existing :meth:`Exporter.to_cogstruction` logic and writes the two
CSVs (``cog_datas.csv`` + ``empties_datas.csv``).
"""

import csv
import logging
from pathlib import Path
from typing import List, Union

from idleon_saver.exporters.base import exporters
from idleon_saver.utility import Sources

logger = logging.getLogger(__name__)


class CogstructionExporter:
    """Export a decoded/firebase save to Cogstruction CSVs."""

    def export(self, savedata: dict, source: Sources, outdir: Union[str, Path]) -> List[Path]:
        """Write ``cog_datas.csv`` and ``empties_datas.csv`` into *outdir*.

        Args:
            savedata: Raw decoded or firebase save dict.
            source: :attr:`Sources.LOCAL` or :attr:`Sources.FIREBASE`.
            outdir: Directory to write the output files into.

        Returns:
            List of written CSV paths.
        """
        exporter = exporters[source](savedata)
        data = exporter.to_cogstruction()
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        written: List[Path] = []
        for which in ["cog_datas", "empties_datas"]:
            outfile = outdir / f"{which}.csv"
            with open(outfile, "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=data[which][0].keys())
                writer.writeheader()
                for row in data[which]:
                    writer.writerow(row)
            logger.info("Wrote file: %s", outfile)
            written.append(outfile)
        return written
