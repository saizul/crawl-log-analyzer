"""Allow ``python -m crawl_log_analyzer``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
