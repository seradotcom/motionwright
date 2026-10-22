"""Exercise API credential boundaries through the production source-policy CLI.

Only the unrelated glib admission is isolated by the existing consumer fixture.
Token values are synthetic and assembled at runtime; no credentials are present.
"""
import json
import unittest

if __package__:
    from . import test_public_surface_policy as policy_fixtures
else:
    import test_public_surface_policy as policy_fixtures


IDENTIFIERS = (
    "models-unknown-task-domain-unit-main-a8d77c9d",
    "models-unknown-task-sqlite-reopen-main-a8d77c9d",
)


class ApiCredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Compose the fixture without inheriting or rediscovering its test cases.
        self.fixture = policy_fixtures.SourcePolicyConsumerTests(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.serial = 0

    @staticmethod
    def tokens():
        body = "A7_b-Q9z" * 3
        return (("legacy", "s" + "k-" + body),
                ("project", "s" + "k-" + "proj-" + body))

    @staticmethod
    def source_bytes(root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

    def check_policy(self, text, *, filename, rejected, token=None):
        self.serial += 1
        root = self.fixture.policy_repo(f"boundary-{self.serial}")
        name = "docs/" + filename
        self.fixture.write(root, name, text)
        self.fixture.git(root, "add", "--", name)
        before = self.source_bytes(root)
        index_before = (root / ".git/index").read_bytes()

        result = self.fixture.run_policy(root)

        self.assertEqual(self.source_bytes(root), before)
        self.assertEqual((root / ".git/index").read_bytes(), index_before)
        if rejected:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                "ERROR: API credential-like token found: " + name + "\n",
            )
            self.assertNotIn(token, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIn("source-policy=ok ", result.stdout)

    def test_immutable_assessment_ids_in_text_are_not_credentials(self):
        self.check_policy("\n".join(IDENTIFIERS) + "\n",
                          filename="assessment-identities.txt", rejected=False)

    def test_immutable_assessment_ids_in_json_are_not_credentials(self):
        document = {"assessments": [{"id": value} for value in IDENTIFIERS]}
        self.check_policy(json.dumps(document, indent=2) + "\n",
                          filename="assessment-identities.json", rejected=False)

    def test_api_keys_at_file_start_and_end_are_rejected(self):
        for form, token in self.tokens():
            with self.subTest(form=form):
                self.check_policy(token, filename="credential.txt",
                                  rejected=True, token=token)

    def test_api_keys_in_json_strings_are_rejected(self):
        for form, token in self.tokens():
            with self.subTest(form=form):
                self.check_policy(json.dumps({"api_key": token}) + "\n",
                                  filename="credential.json", rejected=True, token=token)

    def test_api_keys_after_header_whitespace_are_rejected(self):
        for form, token in self.tokens():
            with self.subTest(form=form):
                self.check_policy("\nAuthorization: Bearer " + token + "\t\n",
                                  filename="credential.txt", rejected=True, token=token)

    def test_api_keys_after_punctuation_are_rejected(self):
        for form, token in self.tokens():
            for left, right in (("=", ";"), (":", ","), ("(", ")"), ("-", ".")):
                with self.subTest(form=form, left=left):
                    self.check_policy(left + token + right, filename="credential.txt",
                                      rejected=True, token=token)


if __name__ == "__main__":
    unittest.main()
