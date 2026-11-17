"""G0 retained-scope guards; no provider, renderer or Native acceptance."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_render_e2e as native


class RetainedScopeGuards(unittest.TestCase):
    def test_xy_rejects_third_job_before_render_start(self):
        context = {"selection":"out-cubic-xy","jobs":["first","second"]}
        with mock.patch.object(native,"CYCLE_CONTEXT",context), \
             mock.patch.object(native,"cycle_execute",side_effect=AssertionError("effect forbidden")) as effect:
            with self.assertRaisesRegex(native.N83RunnerFailure,"JOB_BUDGET_BEFORE_EFFECT"):
                native.cycle_native_render("a"*64,socket="unused",session="unused",env={})
            effect.assert_not_called()
            self.assertEqual(context["jobs"],["first","second"])

    def test_xy_rejects_second_host_before_file_or_process_work(self):
        context = {"selection":"out-cubic-xy","hosts":[1234]}
        with mock.patch.object(native,"CYCLE_CONTEXT",context), \
             mock.patch.object(native.subprocess,"Popen",side_effect=AssertionError("effect forbidden")) as effect:
            with self.assertRaisesRegex(native.N83RunnerFailure,"HOST_BUDGET_BEFORE_EFFECT"):
                native.cycle_start_host("reopened",{},None,{})
            effect.assert_not_called()

    def test_xy_cannot_enter_legacy_edit_or_reopen(self):
        with mock.patch.object(native,"CYCLE_CONTEXT",{"selection":"out-cubic-xy"}), \
             mock.patch.object(native,"cycle_new_stage",side_effect=AssertionError("stage forbidden")) as stage, \
             mock.patch.object(native,"cycle_run_json",side_effect=AssertionError("command forbidden")) as command:
            with self.assertRaisesRegex(native.N83RunnerFailure,"EDITABLE_SOURCE_CYCLE_ONLY"):
                native.cycle_remaining(None,{},None,{},None)
            stage.assert_not_called()
            command.assert_not_called()

    def test_job_registries_keep_distinct_closed_budgets(self):
        for scope, maximum in (("out-cubic-xy",2),("editable-source-cycle",5)):
            context = {"selection":scope,"jobs":[]}
            with self.subTest(scope=scope), mock.patch.object(native,"CYCLE_CONTEXT",context):
                for index in range(maximum):
                    native.cycle_register_job("job-"+str(index))
                before = context["jobs"][:]
                for value in ("job-0","another-job","",None):
                    with self.assertRaisesRegex(native.N83RunnerFailure,"JOB_REUSED_OR_BUDGET"):
                        native.cycle_register_job(value)
                self.assertEqual(context["jobs"],before)

    def test_xy_stage_creation_stops_at_one_original(self):
        context = {"selection":"out-cubic-xy","stages":{},"source":{"g0_only":True}}
        with tempfile.TemporaryDirectory(prefix="mc-xy-stage-g0-") as directory, \
             mock.patch.object(native,"CYCLE_CONTEXT",context), \
             mock.patch.object(native,"EVIDENCE",Path(directory).resolve()):
            path = native.cycle_new_stage("original")
            self.assertTrue((path/"stage.started.original.json").is_file())
            for name in ("original","reopened","edited","edited_reopened","extra"):
                with self.subTest(name=name), self.assertRaisesRegex(native.N83RunnerFailure,"STAGE_REPLAY"):
                    native.cycle_new_stage(name)
            self.assertEqual(list(context["stages"]),["original"])

    def test_rejected_admission_never_reaches_recipient_or_source(self):
        good = {"GITHUB_REPOSITORY":"seradotcom/motionwright",
            "GITHUB_JOB":"real-native-render","GITHUB_WORKFLOW_SHA":"a"*40,
            "GITHUB_ACTIONS":"true","GITHUB_EVENT_NAME":"workflow_dispatch",
            "GITHUB_RUN_ATTEMPT":"1","MOTIONWRIGHT_V01_PROVIDER_REPEAT":"false",
            "MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY":"0"}
        invalid = {"GITHUB_REPOSITORY":"foreign/repo","GITHUB_JOB":"other-job",
            "GITHUB_WORKFLOW_SHA":"b"*40,"GITHUB_ACTIONS":"false",
            "GITHUB_EVENT_NAME":"push","GITHUB_RUN_ATTEMPT":"2",
            "MOTIONWRIGHT_V01_PROVIDER_REPEAT":"true","MOTIONWRIGHT_V01_PROVIDER_EVENT_REPLAY":"1"}
        for key, value in invalid.items():
            with self.subTest(key=key), mock.patch.dict(os.environ,{**good,key:value},clear=True), \
                 mock.patch.object(native,"NATIVE_SCOPE","out-cubic-xy"), \
                 mock.patch.object(native,"GITHUB_SHA","a"*40), \
                 mock.patch.object(native,"RUN_LABEL",""), mock.patch.object(native,"CAPTION_E2E","0"), \
                 mock.patch.object(native,"n83_custody",side_effect=AssertionError("no custody access")) as access:
                with self.assertRaisesRegex(native.N83RunnerFailure,"CLOSED_MANUAL_SELECTION"):
                    native.cycle_admission()
                access.assert_not_called()

    def test_unknown_or_standard_selection_cannot_get_retained_admission(self):
        for selection in ("standard","OUT-CUBIC-XY","out-cubic-xy ","editable"):
            with self.subTest(selection=selection), mock.patch.object(native,"NATIVE_SCOPE",selection), \
                 mock.patch.object(native,"n83_custody",side_effect=AssertionError("no custody access")) as access:
                with self.assertRaisesRegex(native.N83RunnerFailure,"CLOSED_MANUAL_SELECTION"):
                    native.cycle_admission()
                access.assert_not_called()

    def test_xy_finalizer_refuses_live_process_before_opening_originals(self):
        context = {"selection":"out-cubic-xy","completed":["original"],
                   "jobs":["one","two"],"hosts":[1234],"processes":[{"quiescent":False}]}
        with mock.patch.object(native,"CYCLE_CONTEXT",context), \
             mock.patch.object(native,"n83_custody",side_effect=AssertionError("reader forbidden")) as access:
            with self.assertRaisesRegex(native.N83RunnerFailure,"SCOPE_OR_CLOSURE_NOT_VERIFIED"):
                native.cubic_xy_finalize()
            access.assert_not_called()


if __name__ == "__main__":
    unittest.main()
