"""Keep camera integrations from corrupting existing native measured-data fixtures."""
from pathlib import Path
import unittest


EXAMPLE = Path(__file__).resolve().parents[2] / "crates" / "native" / "examples" / "native-render-e2e.rs"


class NativeGoldenLiteralGuard(unittest.TestCase):
    def test_existing_measured_table_native_fixture_labels_are_exact_utf8(self):
        source = EXAMPLE.read_text(encoding="utf-8")
        self.assertEqual(source.count('name: "Explicit 0\u2013100% synthetic bar".into()'), 1)
        self.assertEqual(source.count('if projected_label.text.as_deref() != Some("Synthetic rate \u00b7 37.5%")'), 1)
        self.assertNotIn("Synthetic rate \u00c3", source)
        self.assertNotIn("Explicit 0\u00c3", source)

    def test_original_motion_comment_preserves_unambiguous_source_meaning(self):
        source = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("exact paired source-to-target position transition", source)
        self.assertNotIn("exact paired 0\u00c3", source)


if __name__ == "__main__":
    unittest.main()
