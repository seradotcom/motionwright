"""Resource-scope regression: executable staging must not enlarge Store limits."""
import copy
import os
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_canvas_service as ci


class GraphStagingFileBudgetTests(unittest.TestCase):
    def inputs(self):
        refs = {role: {"path": "/captured/" + role, "sha256": "a" * 64, "size_bytes": 1234}
                for role in ("native_provider", "manim_runner", "python3", "ffmpeg", "semwright_sandbox")}
        refs["native_provider"]["size_bytes"] = 67032280
        return {"binaries": refs}, {"sha256": "b" * 64}

    def test_verified_provider_staging_preserves_store_budget_and_exact_bound(self):
        index, ref = self.inputs()
        budget = ci.graph_case_staging_budget(index, ref)
        self.assertEqual(budget["store_file_bytes"], 4194304)
        self.assertEqual(budget["maximum_file_bytes"], 67032280)
        self.assertEqual(ci.graph_checked_case_staging_budget(budget), 67032280)
        self.assertEqual(budget["producer_index_sha256"], ref["sha256"])
        index["binaries"]["native_provider"]["size_bytes"] += 1
        self.assertEqual(budget["executables"]["native_provider"]["size_bytes"], 67032280)

    @unittest.skipUnless(os.name == "posix", "actual POSIX resource enforcement")
    def test_controller_staging_and_store_child_have_distinct_actual_limits(self):
        index, ref = self.inputs()
        index["binaries"]["native_provider"]["size_bytes"] = 4198400
        budget = ci.graph_case_staging_budget(index, ref)
        child_script = "import resource; resource.setrlimit(resource.RLIMIT_FSIZE,(4194304,4194304))\nwith open('store.bin','wb') as stream: stream.write(b'x'*4194305)"
        script = "import resource,subprocess,sys\n" + \
            "assert resource.getrlimit(resource.RLIMIT_FSIZE)==(4198400,4198400)\n" + \
            "with open('staged.bin','wb') as stream: stream.write(b'x'*4194305)\n" + \
            "child=subprocess.run([sys.executable,'-I','-c'," + repr(child_script) + "])\n" + \
            "assert child.returncode != 0\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = ci.execute([sys.executable, "-I", "-c", script], root, {}, root,
                "graph-cases", 5, maximum_file_bytes=4194304, maximum_stream_bytes=1024,
                staging_file_budget=budget)
            ci.require_observed_command(command)
            self.assertEqual(command["exit_code"], 0, (root / "graph-cases.stderr.log").read_text())
            self.assertEqual((root / "staged.bin").stat().st_size, 4194305)
            self.assertLessEqual((root / "store.bin").stat().st_size, 4194304)
            limits = command["file_size_limits"]
            self.assertEqual(limits["controller_requested_soft_hard_bytes"], 4198400)
            self.assertEqual(limits["required_store_writer_soft_hard_bytes"], 4194304)

    def test_budget_rejects_larger_store_arbitrary_bound_and_invalid_source_sizes(self):
        index, ref = self.inputs()
        budget = ci.graph_case_staging_budget(index, ref)
        for field, value in (("store_file_bytes", 67032280), ("maximum_file_bytes", 67032281)):
            invalid = copy.deepcopy(budget); invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.graph_checked_case_staging_budget(invalid)
        for size in (True, 0, 256 * 1024 * 1024 + 1):
            invalid = copy.deepcopy(index); invalid["binaries"]["native_provider"]["size_bytes"] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                ci.graph_case_staging_budget(invalid, ref)


if __name__ == "__main__": unittest.main()
