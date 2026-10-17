"""Exercise source hygiene against real isolated Git repositories.

Consumer tests isolate glib admission only; they execute the production source
policy and traversal and make no claim about dependency or product acceptance.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_surface_policy import (
    PublicSurfaceError, is_local_development_path, iter_source_files,
    scan_paths, scan_text, verify_public_surface,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOTS = (
    ".agents", ".codex", "knowledge", "worktrees", ".worktrees", "verification",
)
IGNORE_TEXT = "\n".join("/" + name + "/" for name in PRIVATE_ROOTS) + "\n"


class PublicSurfacePolicyTests(unittest.TestCase):
    def test_current_tracked_repository_is_clean(self):
        self.assertEqual(verify_public_surface(SOURCE_ROOT), [])

    def test_private_roots_include_exact_entries_and_descendants(self):
        for name in PRIVATE_ROOTS:
            for path in (name, name + "/some-file.md", name.upper() + "/nested/x"):
                with self.subTest(path=path):
                    self.assertTrue(is_local_development_path(path))
                    self.assertIn(
                        "local_development_file_tracked:" + path, scan_paths([path]),
                    )

    def test_nested_product_paths_are_not_private_roots(self):
        for path in (
            "docs/knowledge/contract.txt", "apps/studio/.agents/provider.py",
            "crates/verification/src/lib.rs", "knowledge-base/README.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_local_development_path(path))
                self.assertEqual(scan_paths([path]), [])

    def test_instruction_names_are_rejected_at_any_depth(self):
        for path in (
            "AGENTS.md", "docs/AGENTS.md", "apps/studio/CLAUDE.md",
            "docs/nested/master_prompt.md", "apps/.cursorrules",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    "local_instruction_file_tracked:" + path, scan_paths([path]),
                )

    def test_real_product_agents_are_not_forbidden(self):
        legitimate = (
            "A human and an agent edit the same revision-bound project.\n"
            "An external_agent provider has an explicit permission boundary.\n"
            "No agent may bypass an owner effect grant.\n"
            "The next agent receives the existing revision and cannot bypass grants.\n"
            "An agent handoff must preserve the same scoped authority."
        )
        for path in ("docs/native-sdk.md", "crates/native/src/lib.rs"):
            self.assertEqual(scan_text(path, legitimate), [])

    def test_development_speech_is_rejected_without_copying_its_body(self):
        for phrase in (
            "Another agent may be implementing this.",
            "human review not performed by the implementation agent",
            "silent modifications to other agents' work",
            "Delegated by the user:",
            "impeccable:product-schema",
            "Use this master prompt",
            "The next agent should edit this module",
        ):
            with self.subTest(phrase=phrase):
                outcome = scan_text("docs/spec.md", phrase)
                self.assertEqual(len(outcome), 1)
                self.assertNotIn(phrase, outcome[0])

    def test_multiline_instructions_report_their_start_line(self):
        for phrase in (
            "Another agent may\nbe implementing this.",
            "The next\nagent should\tedit this module.",
            "Delegated\nby the user:",
        ):
            with self.subTest(phrase=phrase):
                outcome = scan_text("docs/spec.md", "Product contract.\n" + phrase)
                self.assertEqual(len(outcome), 1)
                self.assertTrue(outcome[0].startswith(
                    "public_development_text:docs/spec.md:2:",
                ))

    def test_root_markdown_and_uppercase_extensions_are_checked(self):
        for path in ("CONTRIBUTING.md", "docs/readme.MD"):
            with self.subTest(path=path):
                self.assertTrue(scan_text(path, "The next agent should edit this."))

    def test_private_text_is_not_reported_as_public_text(self):
        self.assertEqual(scan_text(
            "knowledge/internal.md", "Another agent may be working",
        ), [])
        self.assertEqual(scan_text(
            "tests/private-notes.txt", "Agent handoff",
        ), [])


class GitFixtureTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="motionwright-source-policy-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        # Keep subprocesses inside the fixture and independent of host Git state.
        env = os.environ.copy()
        for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
            "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            env.pop(name, None)
        env.update({
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(self.scratch),
            "TMP": str(self.scratch),
            "TMPDIR": str(self.scratch),
        })
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True,
            text=True, encoding="utf-8", timeout=15,
        ).stdout

    def write(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def repo(self, relative="checkout"):
        root = self.scratch / relative
        root.mkdir(parents=True)
        self.git(root, "-c", "init.templateDir=", "init", "--quiet")
        self.write(root, ".gitignore", IGNORE_TEXT)
        self.write(root, "README.md", "Public product contract.\n")
        self.git(root, "add", "--", ".gitignore", "README.md")
        return root


class PublicSurfaceGitTests(GitFixtureTests):
    def test_effective_ignored_untracked_material_is_not_public(self):
        root = self.repo()
        self.write(root, "knowledge/notes.md", "The next agent should edit this.")
        self.assertEqual(verify_public_surface(root), [])

    def test_git_tracked_private_entries_and_descendants_are_rejected(self):
        for index, name in enumerate(PRIVATE_ROOTS):
            for kind, entry in (("root", name), ("child", name + "/notes.txt")):
                with self.subTest(entry=entry):
                    root = self.repo(f"case-{index}-{kind}")
                    self.write(root, entry, "Synthetic local fixture.\n")
                    self.git(root, "add", "--force", "--", entry)
                    self.assertIn(
                        "local_development_file_tracked:" + entry,
                        verify_public_surface(root),
                    )

    def test_git_tracked_nested_instruction_is_rejected(self):
        root = self.repo()
        self.write(root, "docs/AGENTS.md", "Synthetic local instructions.\n")
        self.git(root, "add", "--", "docs/AGENTS.md")
        self.assertIn(
            "local_instruction_file_tracked:docs/AGENTS.md",
            verify_public_surface(root),
        )

    def test_later_negations_cannot_satisfy_effective_ignore(self):
        root = self.repo()
        self.write(
            root, ".gitignore",
            IGNORE_TEXT + "!/knowledge/\n!/knowledge/**\n",
        )
        self.assertTrue(any(
            issue.startswith("ineffective_local_ignore:knowledge/")
            for issue in verify_public_surface(root)
        ))

    def test_invalid_utf8_and_oversized_public_text_are_rejected(self):
        root = self.repo()
        bad = self.write(root, "docs/invalid.md", "placeholder")
        bad.write_bytes(b"\xff\xfe")
        self.write(root, "docs/oversized.md", "x" * 2_000_001)
        self.git(root, "add", "--", "docs/invalid.md", "docs/oversized.md")
        issues = verify_public_surface(root)
        for name in ("docs/invalid.md", "docs/oversized.md"):
            self.assertTrue(any(name in issue for issue in issues), name)

    def test_tracked_public_symlink_is_rejected(self):
        root = self.repo()
        link = root / "docs" / "linked.md"
        link.parent.mkdir()
        try:
            link.symlink_to(root / "README.md")
        except (OSError, NotImplementedError) as exc:
            self.skipTest("This environment cannot create test symlinks: " + str(exc))
        self.git(root, "add", "--", "docs/linked.md")
        self.assertIn("public_text_invalid:docs/linked.md", verify_public_surface(root))

    def test_unavailable_git_cannot_report_success(self):
        root = self.repo()
        with patch("public_surface_policy.subprocess.run", side_effect=OSError):
            with self.assertRaises(PublicSurfaceError):
                verify_public_surface(root)


class SourceTraversalTests(GitFixtureTests):
    def test_walk_prunes_local_roots_but_keeps_nested_source(self):
        root = self.repo()
        included = (
            "docs/knowledge/contract.txt", "apps/studio/.agents/provider.py",
            "crates/verification/src/lib.rs", "docs/worktrees/behavior.txt",
        )
        excluded = (
            "knowledge/internal.txt", ".agents/local.txt", ".codex/hooks.json",
            "worktrees/other/README.md", ".worktrees/old/readme.md",
            "verification/session.json", "target/build.txt",
            "apps/studio/node_modules/package/index.js",
        )
        for name in included + excluded:
            self.write(root, name, "Synthetic fixture.\n")
        observed = {path.relative_to(root).as_posix() for path in iter_source_files(root)}
        self.assertTrue(set(included) <= observed)
        self.assertTrue(set(excluded).isdisjoint(observed))
        self.assertFalse(any(name.startswith(".git/") for name in observed))

    def test_repository_parent_named_worktrees_does_not_hide_source(self):
        root = self.repo("worktrees/checkout")
        self.write(root, "docs/contract.txt", "Observable product behavior.\n")
        observed = {path.relative_to(root).as_posix() for path in iter_source_files(root)}
        self.assertIn("README.md", observed)
        self.assertIn("docs/contract.txt", observed)


class SourcePolicyConsumerTests(GitFixtureTests):
    def policy_repo(self, relative="checkout"):
        root = self.repo(relative)
        revision = "a" * 40
        fixtures = {
            "SOURCE_LOCK.json": json.dumps({
                "dependencies": {"semwright": {"revision": revision, "version": "1.0.0"}},
            }),
            "Cargo.toml": (
                '[dependencies]\nsemwright = { git = "https://github.com/'
                'seradotcom/semwright", rev = "' + revision + '" }\n'
            ),
            "apps/desktop/src-tauri/Cargo.toml": (
                '[package]\nname = "policy-fixture"\nversion = "1.0.0"\n'
            ),
            "apps/desktop/src-tauri/src/main.rs": (
                'const SEMWRIGHT_VERSION: &str = "1.0.0";\n'
                'const REV: &str = "' + revision + '";\nfn main() {}\n'
            ),
            "apps/studio/src/fixture.ts": (
                'export const fixture = {pinned_revision: "' + revision + '"};\n'
            ),
            "apps/desktop/src-tauri/tauri.conf.json": json.dumps({
                "app": {"security": {"csp": (
                    "default-src 'self'; object-src 'none'; frame-src 'none'; "
                    "base-uri 'none'; form-action 'none'; script-src 'self'; "
                    "connect-src ipc: http://ipc.localhost"
                )}},
            }),
            "LICENSE": "GNU AFFERO GENERAL PUBLIC LICENSE\n",
        }
        for name in ("tooling/source_policy.py", "tooling/public_surface_policy.py"):
            fixtures[name] = (SOURCE_ROOT / name).read_text(encoding="utf-8")
        for name, content in fixtures.items():
            self.write(root, name, content)
        self.git(root, "add", "--", *fixtures)
        return root

    def run_policy(self, root):
        # The unrelated glib admission dependency has its own independent suites.
        # Source traversal, public-surface validation and credential checks are real.
        bootstrap = (
            "import pathlib, runpy, sys, types\n"
            "root = pathlib.Path(sys.argv[1])\n"
            "sys.path.insert(0, str(root / 'tooling'))\n"
            "dependency = types.ModuleType('v01_glib_product_admission')\n"
            "dependency.admit_patched_glib = lambda root: None\n"
            "sys.modules['v01_glib_product_admission'] = dependency\n"
            "runpy.run_path(str(root / 'tooling/source_policy.py'), run_name='__main__')\n"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", bootstrap, str(root)],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_valid_minimal_consumer_fixture_passes(self):
        result = self.run_policy(self.policy_repo())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source-policy=ok", result.stdout)

    def test_nested_local_names_cannot_hide_credential_findings(self):
        # Deliberately synthetic and assembled so the test source is not a token.
        token = "gh" + "p_" + "Q" * 24
        for index, name in enumerate((
            "docs/knowledge/credentials.txt", "apps/studio/.agents/provider.py",
        )):
            with self.subTest(name=name):
                root = self.policy_repo(f"nested-{index}")
                self.write(root, name, token + "\n")
                self.git(root, "add", "--", name)
                result = self.run_policy(root)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("GitHub credential-like token found: " + name, output)
                self.assertNotIn(token, output)

    def test_parent_worktrees_directory_cannot_hide_credential_findings(self):
        root = self.policy_repo("worktrees/consumer")
        name = "docs/credential-fixture.txt"
        self.write(root, name, "gh" + "p_" + "Q" * 24 + "\n")
        self.git(root, "add", "--", name)
        result = self.run_policy(root)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "GitHub credential-like token found: " + name, result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
