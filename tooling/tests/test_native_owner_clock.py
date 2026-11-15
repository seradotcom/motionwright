"""Pure clock regression controls; no Native admission or process is claimed."""
import importlib.util
import json
import math
from pathlib import Path
import unittest

TOOLING = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("owner_clock_focal", TOOLING / "native_owner_resources.py")
resource = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource)


class OriginalClockTests(unittest.TestCase):
    def test_authenticated_case_clock_preserves_exact_issued_deadline(self):
        # Run 43699695958, original case01 observed_monotonic. The issuer stores
        # this start and start + 3900; the subtraction loses one rounding bit.
        original = json.loads('{"started_monotonic":1096.094195524,"deadline_monotonic":4996.094195524}')
        start, end = original["started_monotonic"], original["deadline_monotonic"]
        self.assertEqual(end - start, 3899.9999999999995)
        resource.validate_original_clock_window(start, end, 3900, start + 0.001)

    def test_adjacent_representable_deadline_is_rejected(self):
        start, end = 1096.094195524, 4996.094195524
        for altered in (math.nextafter(end, -math.inf), math.nextafter(end, math.inf)):
            with self.subTest(deadline=altered), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(start, altered, 3900, 1100)

    def test_real_expiration_future_start_and_budget_limits_remain_rejected(self):
        for start, end, seconds, now in (
            (100, 200, 100, 200), (100, 200, 100, 201),
            (100, 200, 100, 99), (-1, 99, 100, 0),
            (100, 100, 0, 100), (100, 99, -1, 100),
            (100, 4001, 3901, 101),
        ):
            with self.subTest(start=start, seconds=seconds, now=now), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(start, end, seconds, now)

    def test_non_finite_boolean_and_wrong_type_inputs_are_rejected(self):
        invalid = (math.nan, math.inf, -math.inf, True, None, "100")
        for value in invalid:
            with self.subTest(field="start", value=value), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(value, 200, 100, 150)
            with self.subTest(field="end", value=value), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(100, value, 100, 150)
        for seconds in (True, 100.0, "100", None):
            with self.subTest(seconds=seconds), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(100, 200, seconds, 150)

    def test_expired_inspection_does_not_forgive_changed_duration_or_future_start(self):
        resource.validate_original_clock_window(100, 200, 100, 250, allow_expired=True)
        for start, end in ((100, math.nextafter(200, math.inf)), (300, 400)):
            with self.subTest(start=start, end=end), self.assertRaises(resource.ResourceRejected):
                resource.validate_original_clock_window(start, end, 100, 250, allow_expired=True)


if __name__ == "__main__":
    unittest.main()
