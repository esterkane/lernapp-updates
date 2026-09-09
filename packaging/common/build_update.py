"""Build a source-only update archive using the same public payload as all installers."""

import json
import tomllib
import zipfile
from pathlib import Path

from source_payload import MANIFEST, files, hashes

root = Path(__file__).resolve().parents[2]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
dist = root / "dist"
dist.mkdir(exist_ok=True)
with zipfile.ZipFile(dist / "lernapp-update.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in files(root):
        z.write(p, p.relative_to(root).as_posix())
    z.writestr(MANIFEST, json.dumps({"version": version, "files": hashes(root)}, sort_keys=True))
print("Built public update package:", version)
