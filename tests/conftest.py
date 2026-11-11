"""Suite-wide isolation: tests must never touch the owner's real runtime files.

Before this existed, every test run appended `sleep 5` executions to the real
logs/audit.log (breaking SC4 on paper) and overwrote avatar_state.json. The
data dir is redirected at import time — before any jarvis logger is created.
"""
import os
import tempfile

os.environ["DIANE_DATA_DIR"] = tempfile.mkdtemp(prefix="diane-test-")
