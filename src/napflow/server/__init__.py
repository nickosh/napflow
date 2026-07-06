"""napflow.server — BlackSheep adapter over core (S4, FR-1001).

Thin by contract: core never imports this package (NFR-01), this
package never imports the CLI. `build_app(workspace)` is the whole
public surface; `napf ui` and the tests both go through it.
"""

from napflow.server.app import build_app

__all__ = ["build_app"]
