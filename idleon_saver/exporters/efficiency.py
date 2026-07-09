"""IdleonEfficiency exporter.

IdleonEfficiency ingests the same raw cloud-save JSON as IdleonToolbox, but
expects it under a top-level ``Cloudsave`` key. So this exporter re-emits the
raw cloud save wrapped in ``{"Cloudsave": <data>}``:

* **firebase source** -> pass-through wrapped in ``Cloudsave``.
* **local source** -> best-effort unwrap wrapped in ``Cloudsave`` (documented
  limitation, RQ-5).
"""

import json
import logging
from pathlib import Path
from typing import Union

from idleon_saver.utility import Sources

logger = logging.getLogger(__name__)


class EfficiencyExporter:
    """Re-emit the raw cloud-save JSON (Cloudsave envelope) for IdleonEfficiency."""

    def export(self, savedata: dict, source: Sources, outdir: Union[str, Path]) -> Path:
        """Write the raw cloud-save JSON wrapped in a Cloudsave envelope.

        Args:
            savedata: Raw decoded or firebase save dict.
            source: :attr:`Sources.LOCAL` or :attr:`Sources.FIREBASE`.
            outdir: Directory to write the output file into.

        Returns:
            Path to the written ``idleon_efficiency.json``.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        outfile = outdir / "idleon_efficiency.json"

        # Firebase source = pass-through; local source = best-effort unwrap.
        data = savedata if source == Sources.FIREBASE else dict(savedata)
        with open(outfile, "w", encoding="utf-8") as file:
            json.dump({"Cloudsave": data}, file)
        logger.info("Wrote file: %s", outfile)
        return outfile
