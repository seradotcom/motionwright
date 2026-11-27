"""Guard the isolated KXMLGUI override before touching native Kdenlive."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kdenlive_macos_kxmlgui as profile


class MacToolbarOverrideTests(unittest.TestCase):
    def test_only_the_exact_timeline_toolbar_action_is_inserted(self):
        original = (
            b'<kpartgui name="kdenlive" version="250">\n'
            b'<ToolBar name="mainToolBar"><Action name="file_open"/></ToolBar>\n'
            b'<ToolBar name="timelineToolBar">\n'
            b'    <text>Timeline Toolbar</text>\n'
            b'    <Action name="existing"/>\n'
            b'</ToolBar>\n</kpartgui>\n'
        )
        digest = sha256(original).hexdigest()
        with patch.object(profile, "KDE_XML_SHA256", digest), patch.object(profile, "KDE_XML_BYTES", len(original)):
            override = profile.toolbar_override(original)
        self.assertEqual(override.replace(profile.VIDEO_TRACK_ACTION, b"", 1), original)
        self.assertEqual(override.count(b'name="activate_video_1"'), 1)

    def test_ambiguous_or_different_preimage_cannot_create_an_override(self):
        with self.assertRaisesRegex(ValueError, "preimage"):
            profile.toolbar_override(b'<kpartgui name="kdenlive" />')

    def test_foundation_default_home_stops_before_fetching_or_writing_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            environment = {"HOME": "/fixed-default"}
            with patch.object(profile, "probe_foundation", return_value={
                "home": "/fixed-default", "application_support": ["/fixed-default/Library/Application Support"],
            }), patch.object(profile, "download_xml") as fetch:
                with self.assertRaisesRegex(ValueError, "Foundation"):
                    profile.prepare_profile(output, environment)
            self.assertEqual(environment, {"HOME": "/fixed-default"})
            fetch.assert_not_called()
            self.assertFalse(any(output.rglob("*.rc")))
            self.assertTrue((output / "kxmlgui-foundation-paths.json").exists())


if __name__ == "__main__":
    unittest.main()
