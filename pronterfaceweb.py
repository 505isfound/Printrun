#!/usr/bin/env python3
# Starts Pronterface Web (the new web-based interface).
# The classic app is still pronterface.py.
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "web"))
sys.path.insert(0, here)

from pronterface_web.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
