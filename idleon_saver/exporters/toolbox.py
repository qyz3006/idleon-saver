"""IdleonToolbox exporter.

IdleonToolbox ingests the *raw* cloud-save JSON (the ``firebase.json`` shape:
a flat ``key -> JSON-string`` mapping). Therefore this exporter simply
re-emits that JSON:

* **firebase source** -> pass-through (byte-identical to the cloud save).
* **local source** -> best-effort unwrap of the decoded JSON. This is NOT
  byte-identical to a real cloud save and is documented as a limitation
  (RQ-5).
"""

import json
import logging
from pathlib import Path
from typing import Union

from idleon_saver.utility import Sources

logger = logging.getLogger(__name__)


class ToolboxExporter:
    """Re-emit the raw cloud-save JSON for IdleonToolbox."""

    def export(self, savedata: dict, source: Sources, outdir: Union[str, Path]) -> Path:
        """Write the raw cloud-save JSON into *outdir*.

        Args:
            savedata: Raw decoded or firebase save dict.
            source: :attr:`Sources.LOCAL` or :attr:`Sources.FIREBASE`.
            outdir: Directory to write the output file into.

        Returns:
            Path to the written ``idleon_toolbox.json``.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        outfile = outdir / "idleon_toolbox.json"

        # Firebase source = pass-through; local source = best-effort unwrap.
        data = savedata if source == Sources.FIREBASE else dict(savedata)
        with open(outfile, "w", encoding="utf-8") as file:
            json.dump(data, file)
        logger.info("Wrote file: %s", outfile)
        return outfile
