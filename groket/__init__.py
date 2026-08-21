"""groket — Trace evaluation for hunting bad model behaviors.

* Root: ``models``, ``config``, ``parser``, ``paths``, ``constants``, ``utils``, ``flags``, ``cli``
* ``runs/`` — personas, run configs, batch, background manager, shares, log services
* ``session/`` — usage stats and workspace diffs
* ``capabilities/``, ``docker/``
* ``extensions/`` — ``groket gen`` scaffolding
* ``ui/`` — Textual presentation

Data flow: models/parser → runs|session → ui.
"""

from __future__ import annotations

__version__ = "0.1.0"
