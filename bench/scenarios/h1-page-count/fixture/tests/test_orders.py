import unittest

from shopkit.orders import footer, list_orders, make_order, render_page, total_value
from shopkit.outbox import send_ready


def many(n):
    return [make_order(i, "cust", 1000 + i) for i in range(1, n + 1)]


class OrderListTests(unittest.TestCase):
    def test_last_partial_page_is_counted(self):
        listing = list_orders(many(45), page=1, per_page=20)
        self.assertEqual(listing["pages"], 3)
        self.assertEqual(footer(listing), "Page 1 of 3")

    def test_last_page_has_the_rest(self):
        listing = list_orders(many(45), page=3, per_page=20)
        self.assertEqual([o["id"] for o in listing["items"]], [41, 42, 43, 44, 45])

    def test_exact_multiple(self):
        self.assertEqual(list_orders(many(40), per_page=20)["pages"], 2)

    def test_render(self):
        text = render_page(list_orders([make_order(7, "ada", 12345, "paid")]))
        self.assertTrue(text.endswith("Page 1 of 1"))
        self.assertIn("123.45", text)

    def test_total(self):
        self.assertEqual(total_value(many(3)), 3006)


class OutboxTests(unittest.TestCase):
    def test_full_batches_only_when_exact(self):
        sent = []
        result = send_ready(list(range(40)), 20, sent.append)
        self.assertEqual(result, {"sent": 2, "held": []})
        self.assertEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
