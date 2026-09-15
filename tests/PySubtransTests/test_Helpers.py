from PySubtrans.Helpers import FormatNumberRanges
from PySubtrans.Helpers.TestCases import LoggedTestCase


class TestHelperFunctions(LoggedTestCase):
    """Test generic helper functions."""

    def test_FormatNumberRanges(self) -> None:
        test_cases = [
            ([], ""),
            ([7], "7"),
            ([1, 2, 3, 5, 6, 9], "1-3, 5-6, 9"),
            ([19, 1, 2, 2, 3, 30], "1-3, 19, 30"),
            ([3, 2, 1], "1-3"),
        ]

        for numbers, expected in test_cases:
            with self.subTest(numbers=numbers):
                result = FormatNumberRanges(numbers)
                self.assertLoggedEqual(
                    "format number ranges",
                    expected,
                    result,
                    input_value=numbers,
                )
