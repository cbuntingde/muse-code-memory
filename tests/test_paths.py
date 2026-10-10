import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import IsolatedCase  # noqa: E402
from muse_memory import paths  # noqa: E402


class PathsTest(IsolatedCase):
    def test_data_dir_uses_xdg(self) -> None:
        self.assertEqual(paths.muse_data_dir(),
                         self.data_home / "muse")

    def test_config_dir_uses_xdg(self) -> None:
        self.assertEqual(paths.muse_config_dir(),
                         self.config_home / "muse")

    def test_slug_deterministic(self) -> None:
        a = paths.slug_for(self.work)
        b = paths.slug_for(self.work)
        self.assertEqual(a, b)
        self.assertIn("-", a)
        self.assertLessEqual(len(a), 80 + 13)

    def test_slug_differs_per_workspace(self) -> None:
        other = self.work.parent / "other"
        other.mkdir()
        self.assertNotEqual(paths.slug_for(self.work), paths.slug_for(other))

    def test_slug_adopts_native_dir(self) -> None:
        stem = paths.slug_for(self.work).rsplit("-", 1)[0]
        native = paths.projects_root() / f"{stem}-deadbeefcafe"
        native.mkdir(parents=True)
        self.assertEqual(paths.slug_for(self.work), native.name)

    def test_home_dir_absolute_with_cleared_home_env(self) -> None:
        """Regression: cleared home env must never yield a relative '~'."""
        for var in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                    "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            os.environ.pop(var, None)
        try:
            home = paths.home_dir()
        except RuntimeError:
            return  # honest failure beats writing to a relative "~" dir
        self.assertTrue(home.is_absolute(), home)
        self.assertNotIn("~", home.parts)
        data = paths.muse_data_dir()
        self.assertTrue(data.is_absolute(), data)
        self.assertNotIn("~", data.parts)

    def test_relative_xdg_values_ignored(self) -> None:
        os.environ["XDG_DATA_HOME"] = "relative/data"
        os.environ["XDG_CONFIG_HOME"] = "relative/config"
        self.assertTrue(paths.muse_data_dir().is_absolute())
        self.assertTrue(paths.muse_config_dir().is_absolute())

    def test_shared_dir_none_outside_repo(self) -> None:
        self.assertIsNone(paths.shared_project_dir(self.work))

    def test_shared_dir_inside_repo(self) -> None:
        (self.work / ".git").mkdir()
        self.assertEqual(paths.shared_project_dir(self.work),
                         self.work / ".agents" / "memory")


if __name__ == "__main__":
    unittest.main()
