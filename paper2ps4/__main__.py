"""Allow `python -m paper2ps4`."""
import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
