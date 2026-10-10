"""Shared test helpers: isolated XDG dirs + temp workspace."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class IsolatedCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="muse-mem-test-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.data_home = base / "data"
        self.config_home = base / "config"
        self.work = base / "work"
        self.work.mkdir()
        self._old_env = dict(os.environ)
        os.environ["XDG_DATA_HOME"] = str(self.data_home)
        os.environ["XDG_CONFIG_HOME"] = str(self.config_home)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._old_env)
