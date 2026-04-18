"""odh-codeserver: pre-compiled code-server standalone release for ODH workbench images."""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "4.106.3"


def get_install_path() -> Path:
    """Return the path where the code-server release-standalone tree is installed.

    Hatchling's shared-data places the tree under ``{prefix}/share/odh-codeserver/``.
    Falls back to ``odh_codeserver/data/`` inside the package directory for
    in-tree / editable installs.
    """
    prefix = Path(sys.prefix) / "share" / "odh-codeserver"
    if prefix.is_dir():
        return prefix

    return Path(__file__).parent / "data"
