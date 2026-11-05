"""Entry point: `streamlit run streamlit_app.py`.

Streamlit runs its target as a script rather than importing it as part of a
package, so a page living inside `cbb/` could not use the package's own
imports. This file sits at the repository root, where `cbb` is importable, and
does nothing but hand over to the page.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cbb.streamlit_app import main  # noqa: E402 - the path has to be set first

main()
