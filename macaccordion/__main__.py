"""让 `python -m macaccordion` 直接启动。"""

from .engine import main

if __name__ == "__main__":
    raise SystemExit(main())
