"""Load the original distribution producer contracts through the existing runner."""
from tooling.ci_suites.nsis_source_selection import select_original_suite


def load_tests(loader, standard_tests, pattern):
    return select_original_suite("test_distribution_build_record", __name__)
