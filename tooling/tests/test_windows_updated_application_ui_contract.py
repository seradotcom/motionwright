"""Source contracts for a controller whose native execution remains separate."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class UpdatedApplicationUiContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = (ROOT / "tooling/windows_updated_application_ui.ps1").read_text(encoding="utf-8")
        cls.reader = (ROOT / "tooling/windows_updated_application.py").read_text(encoding="utf-8")
        cls.brief = (ROOT / "apps/studio/src/BriefWorkspace.tsx").read_text(encoding="utf-8")
        cls.library = (ROOT / "apps/studio/src/ProjectLibraryDialog.tsx").read_text(encoding="utf-8")
        cls.app = (ROOT / "apps/studio/src/App.tsx").read_text(encoding="utf-8")

    def test_title_control_and_original_ack_exist_in_the_rendered_workspace(self):
        self.assertIn('id="project-title-rich"', self.brief)
        self.assertIn('htmlFor="project-title-rich"', self.brief)
        self.assertIn("Project title", self.brief)
        self.assertIn("Save title", self.brief)
        self.assertIn("The original save reply and saved revision r", self.brief)
        self.assertIn("The original save reply and saved revision r", self.controller)
        self.assertIn("RichBriefView", self.app)

    def test_selected_uuid_marker_is_a_product_contract_not_inferred_from_title(self):
        self.assertIn("{item.id}", self.library)
        self.assertIn('Current</span>', self.library)
        self.assertIn("currentProjectId", self.library)
        self.assertIn("ProjectId", self.controller)
        self.assertIn("Assert-Row $row $wantedTitle $wantedRevision $true", self.controller)
        self.assertLess(self.controller.index("$Result.selection_confirmed = $true"),
                        self.controller.index("$Result.phase = 'PREPARE_ONE_TITLE'"))

    def test_library_open_close_and_paginated_identity_are_source_defined(self):
        for name in ("Open project library", "Close project library", "Saved projects",
                     "Load more projects"):
            self.assertIn(name, self.app + self.library)
            self.assertIn(name, self.controller)
        # Loading changes the button label; disappearing old labels cannot be
        # treated as completion. Read the advanced UUID roster before another page.
        self.assertIn('loading === "more"', self.library)
        self.assertIn("($nextIdentities -join '|') -cne $observed", self.controller)
        self.assertIn("if (-not $advanced)", self.controller)

    def test_preexisting_uncommitted_inputs_and_unknown_operations_block_edit(self):
        self.assertIn("fields with local input.", self.brief)
        self.assertIn("'0 fields with local input.'", self.controller)
        for name in ("Unconfirmed brief submission", "Brief change not sent",
                     "Brief draft backup warning", "Brief conflicts"):
            self.assertIn(name, self.brief)
            self.assertIn(name, self.controller)
        self.assertLess(self.controller.index("Existing uncommitted Brief inputs"),
                        self.controller.index("[System.Windows.Forms.SendKeys]::SendWait"))

    def test_read_only_branch_returns_before_keyboard_delivery_or_save(self):
        readback = self.controller.index("$Result.phase = 'BRIEF_READBACK'")
        observe = self.controller.index("if ($Mode -eq 'observe')", readback)
        returned = self.controller.index("exit 0", observe)
        keyboard = self.controller.index("[System.Windows.Forms.SendKeys]::SendWait")
        save = self.controller.index("$invoke.Invoke()")
        self.assertLess(returned, keyboard)
        self.assertLess(keyboard, save)
        self.assertNotIn("Invoke-Once", self.controller[observe:returned])

    def test_one_save_dispatch_is_preceded_by_unknown_outcome(self):
        self.assertEqual(self.controller.count("$invoke.Invoke()"), 1)
        self.assertEqual(self.controller.count("[System.Windows.Forms.SendKeys]::SendWait"), 1)
        dispatch = self.controller.index("$invoke.Invoke()")
        before = self.controller[max(0, dispatch - 180):dispatch]
        self.assertIn("$Result.save_attempts = 1", before)
        self.assertIn("$Result.state = 'UNKNOWN'", before)
        after = self.controller[dispatch + len("$invoke.Invoke()"):]
        self.assertNotIn("Invoke-Once", after)
        self.assertNotIn("SendWait", after)

    def test_original_ack_is_not_substituted_by_invoke_completion(self):
        dispatch = self.controller.index("$invoke.Invoke()")
        observed = self.controller.index("if ($acknowledgments.Count -eq 1)", dispatch)
        ack = self.controller.index("$Result.state = 'ACK'", observed)
        guarded = self.controller[observed:ack]
        self.assertIn("Reject-BriefBlockers", guarded)
        self.assertIn("Title-Value", guarded)
        self.assertIn("$ExpectedRevision + 1", guarded)
        self.assertLess(self.controller.index("Future ACK was already visible"), dispatch)

    def test_late_trap_preserves_known_ack_and_emits_original(self):
        trap = self.controller[self.controller.index("trap {"):self.controller.index("if ($env:GITHUB_ACTIONS")]
        self.assertIn("$Result.state -ne 'ACK'", trap)
        self.assertIn("Write-Original", trap)
        self.assertIn("exit 75", trap)

    def test_native_action_is_bound_to_process_creation_and_exact_owned_window(self):
        guard = self.controller[self.controller.index("function Assert-OriginalWindow"):
                                self.controller.index("function Elements")]
        for token in ("GetProcessById($ProcessId)", "ToFileTimeUtc()", "$CreationFiletime",
                      "NativeWindowHandle", "$WindowHandle", "ProcessId", "IsOffscreen"):
            self.assertIn(token, guard)
        self.assertIn("process._handle", self.reader)
        self.assertIn("QueryFullProcessImageNameW", self.reader)
        self.assertNotIn("OpenProcess(", self.reader)

    def test_oracle_has_no_installer_recovery_or_build_dispatch(self):
        for forbidden in ("cargo build", "makensis", "Start-Process", "msiexec", "taskkill",
                          "Stop-Process", "-Verb RunAs", "runas"):
            self.assertNotIn(forbidden, self.controller)
        self.assertIn("gate.parse_checkpoint", self.reader)
        self.assertIn("gate.validate_live_context", self.reader)
        self.assertIn("gate.validate_termination", self.reader)
        self.assertIn("emit_predecessor_include", self.reader)
        self.assertNotRegex(self.reader, r"subprocess\.(?:run|Popen)\(\[.*setup")

    def test_sqlite_reader_preserves_wal_recovery_on_inspection_copies(self):
        self.assertIn('database.as_uri() + "?mode=ro"', self.reader)
        self.assertIn('PRAGMA query_only=ON', self.reader)
        self.assertNotIn('?immutable=1', self.reader)
        self.assertIn('directory / "inspection"', self.reader)

    def test_results_do_not_confer_human_or_release_acceptance(self):
        self.assertIn('"human_acceptance": False', self.reader)
        self.assertIn('"release_authorized": False', self.reader)
        self.assertIn('"installer_execution": "NOT_RUN"', self.reader)


if __name__ == "__main__":
    unittest.main()
