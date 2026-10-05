import os

import pytest

os.environ.setdefault("RESUME_STRICT", "1")   # sub-stage exceptions must fail tests, not become warnings
