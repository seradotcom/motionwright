"""Tiny reader admission regressions; no private files, rendering, or Native calls."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

READERS = Path(__file__).resolve().parents[1] / "fixedtxt_readback"


def load_reader(name):
    spec = importlib.util.spec_from_file_location(name, READERS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preparation = load_reader("preparation_session")
physical = load_reader("review_fixedtxt_result_matrix_gn1_bound")
SAFE_MEMORY_FIELDS = {
    "available_physical_bytes", "estimated_working_bytes",
    "maximum_working_bytes", "reserved_physical_bytes", "admitted",
}


class ReaderResourceAdmissionTests(unittest.TestCase):
    def exercise(self, module, admitted):
        args = SimpleNamespace(
            source_root="source", expected_source="a" * 40, expected_sdk="b" * 40,
            expected_sdk_tree="c" * 40, run_id="1", arm="RED", fixture="flow-text-control",
            envelope="envelope", envelope_sha256="d" * 64, private_key="private-key",
            output_dir="output", bounded_helper="bounded", approved_helper="approved",
            io_adapter="adapter", io_adapter_sha256="e" * 64, p1_helper="p1",
            prepared_payload="payload", prepared_payload_sha256=module.sha(b"{}"),
            run_original="run", jobs_original="jobs", session_index=None,
            first_physical_receipt=None, first_physical_receipt_sha256=None,
            first_external_dispatch=None, first_external_dispatch_sha256=None,
            physical_reader=None, physical_reader_sha256=None,
            public_original="public", public_sha256=module.sha(b"public"),
            external_dispatch="dispatch", external_dispatch_sha256=module.sha(b"dispatch"),
            result_adapter="result-adapter", result_adapter_sha256="f" * 64,
            preparation_reader="preparation", typed_probe_adapter="typed",
            numeric_oracle="numeric", numeric_spec="spec", ffmpeg="decoder",
            ffmpeg_sha256="0" * 64, transform_oracle=None, transform_spec=None)
        plan = {
            "available_physical_bytes": 3 * 1024 ** 3 if admitted else 1024 ** 3,
            "estimated_working_bytes": 900 * 1024 ** 2,
            "maximum_working_bytes": 1536 * 1024 ** 2,
            "reserved_physical_bytes": 512 * 1024 ** 2,
            "admitted": admitted,
            "metadata_and_runtime_allowance_bytes": 512 * 1024 ** 2,
        }
        key_reads = []

        def read(path, *_, **__):
            name = Path(path).name
            if name == "private-key":
                key_reads.append(name)
                raise ValueError("synthetic_key_boundary")
            return {"payload": b"{}", "run": b"{}", "jobs": b"{}", "spec": b"spec",
                    "public": b"public", "dispatch": b"dispatch",
                    "envelope": b"prefix"}.get(name, b"reader")

        base = SimpleNamespace(
            absolute_e=lambda value, **_: Path(value), read_stable=read,
            strict=lambda raw, *_: json.loads(raw), MAGIC=b"magic",
            MAX_ENVELOPE=256 * 1024 ** 2,
            envelope_reference=lambda *_: {"bytes": 44 * 1024 ** 2},
            header_from_prefix=lambda *_: {"plaintext_bytes": 43 * 1024 ** 2})
        bounded = SimpleNamespace(
            ProcessMemoryLimit=Mock(), require_reserve=Mock(),
            load_approved_helper=Mock(return_value=base),
            available_memory=Mock(return_value=plan["available_physical_bytes"]),
            memory_plan=Mock(return_value=plan), decrypt_retained=Mock())
        request = {"source_tree": "c" * 40, "review_capsule_bytes": 256 * 1024 ** 2}
        route = SimpleNamespace(checked_request=Mock(return_value=request))
        dispatch = {"recipient": {"sha256": module.RECIPIENT},
                    "fixedtxt": {"limits": {"capsule_bytes": 256 * 1024 ** 2}}}
        fixed = SimpleNamespace(source_paths=lambda: (), validate_dispatch=lambda *_: dispatch)
        source_lock = SimpleNamespace(load_source_lock=Mock(), verify_source_lock=Mock())
        modules = {
            "ci_private_evidence": SimpleNamespace(), "native_evidence_projection": SimpleNamespace(),
            "native_fixedtxt_g2_admission": fixed, "native_fixedtxt_workflow": route,
            "native_mlt_g2_observation": SimpleNamespace(), "native_mlt_g2_custody": SimpleNamespace(),
            "source_lock_admission": source_lock, "native_fixedtxt_session": SimpleNamespace(),
        }
        spec = {"fixtures": {key: {"selected_frames": list(frames)}
                             for key, frames in module.FIXTURE_FRAMES.items()}}
        numeric = SimpleNamespace(load_spec=lambda *_: spec)
        typed = SimpleNamespace(FIXTURES=module.FIXTURE_FRAMES)
        seam = SimpleNamespace(make_oracle=lambda *_: (Mock(), bounded, Mock(), {}))
        adapter = SimpleNamespace(make_oracle=lambda *_: (Mock(), {}))
        dependencies = {
            "fixedtxt_bounded_io": seam, "fixedtxt_result_bounded_base": bounded,
            "fixedtxt_result_original_reader": SimpleNamespace(),
            "fixedtxt_result_storage_seam": adapter,
            "fixedtxt_frozen_numeric_oracle": numeric, "fixedtxt_original_typed_probe": typed,
        }
        outputs = {}
        output = Mock()
        output.write.side_effect = lambda name, state: outputs.update({name: copy.deepcopy(state)})
        output_class = "PreparationOutput" if module is preparation else "ReadbackOutput"
        output_factory = Mock(return_value=output)
        stdout = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module.argparse.ArgumentParser, "parse_args", return_value=args))
            stack.enter_context(patch.object(module, "os", SimpleNamespace(name="nt")))
            stack.enter_context(patch.object(module, "load", side_effect=lambda _, __, name: dependencies[name]))
            stack.enter_context(patch.object(module, "importlib", SimpleNamespace(import_module=lambda name: modules[name])))
            stack.enter_context(patch.object(module, "relevant_source_identity",
                                              return_value={"render_source_tree": "c" * 40}))
            stack.enter_context(patch.object(module, "matching_source_modules", return_value=SimpleNamespace()))
            stack.enter_context(patch.object(sys, "meta_path", sys.meta_path[:]))
            stack.enter_context(patch.object(module, output_class, output_factory))
            if module is preparation:
                stack.enter_context(patch.object(module, "selected_request", return_value=b"{}"))
                stack.enter_context(patch.object(module, "checked_selection"))
                stack.enter_context(patch.object(module, "live_deadline", return_value={"deadline": 1234}))
            else:
                stack.enter_context(patch.object(module, "SPEC_SHA", module.sha(b"spec")))
                stack.enter_context(patch.object(module, "decoder_reference"))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            result = module.main()

        summary = json.loads(stdout.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(summary["phase"], "authenticated_original_decryption" if admitted
                         else "envelope_and_memory_admission")
        code = summary.get("error_code", summary.get("code"))
        self.assertEqual(code, "synthetic_key_boundary" if admitted else
                         "bounded_memory_not_admitted" if module is preparation
                         else "physical_reader_memory_not_admitted")
        self.assertEqual(summary["memory_preflight"], {k: plan[k] for k in SAFE_MEMORY_FIELDS})
        self.assertNotIn("private-key", stdout.getvalue())
        self.assertEqual(summary["native_calls"], 0)
        self.assertFalse(summary["automatic_retry"])
        self.assertEqual(key_reads, ["private-key"] if admitted else [])
        bounded.memory_plan.assert_called_once_with(43 * 1024 ** 2, plan["available_physical_bytes"])
        bounded.decrypt_retained.assert_not_called()
        if module is preparation:
            terminal = outputs["review.terminal.json"]
            self.assertEqual(terminal["memory_preflight"], plan)
            self.assertFalse(terminal["R28_issued"])
            self.assertNotIn("dispatch.original.json", outputs)
        elif not admitted:
            output_factory.assert_not_called()

    def test_preparation_refusal_reports_budget_before_key_access(self):
        self.exercise(preparation, False)

    def test_preparation_admission_still_reaches_key_boundary(self):
        self.exercise(preparation, True)

    def test_physical_refusal_reports_actual_phase_before_output_or_key(self):
        self.exercise(physical, False)

    def test_physical_admission_still_reaches_key_boundary(self):
        self.exercise(physical, True)


if __name__ == "__main__":
    unittest.main()
