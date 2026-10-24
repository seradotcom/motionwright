"""Load the original contract suite without a second process runner."""
from tooling.ci_suites.nsis_source_selection import select_original_suite


def load_tests(loader, standard_tests, pattern):
    return select_original_suite("test_nsis_owned_files", __name__)
