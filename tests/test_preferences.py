import json
from pathlib import Path
import tempfile
import unittest

from preferences import Preferences


class PreferenceTests(unittest.TestCase):
    def test_missing_corrupt_or_unsupported_layout_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = Preferences(Path(folder) / "ui.json")
            self.assertEqual(preferences.load(), {})
            for text in ("[", "[]", '{"version":99}'):
                preferences.path.write_text(text, encoding="utf-8")
                self.assertEqual(preferences.load(), {})

    def test_save_creates_parent_and_replaces_previous_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = Preferences(Path(folder) / "settings" / "ui.json")
            preferences.save({"description": 220}, True)
            preferences.save({"description": 330}, False)
            saved = json.loads(preferences.path.read_text(encoding="utf-8"))
            self.assertEqual(saved["columns"]["description"], 330)
            self.assertFalse(saved["show_route_metric"])
            self.assertEqual(list(preferences.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
