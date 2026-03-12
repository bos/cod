"""Package entrypoint for `python -m jj_review`."""

from __future__ import annotations

from jj_review.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
