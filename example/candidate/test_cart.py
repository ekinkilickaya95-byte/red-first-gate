import unittest
from cart import total, cheapest


class CartTests(unittest.TestCase):
    def test_total_plain(self):
        self.assertEqual(total([10, 20]), 30)

    def test_total_discount_capped(self):
        self.assertEqual(total([100], discount_percent=150), 0)

    def test_cheapest_empty_is_none(self):
        self.assertIsNone(cheapest([]))

    def test_cheapest_picks_minimum(self):
        self.assertEqual(cheapest([5, 3, 9]), 3)


if __name__ == "__main__":
    unittest.main()
