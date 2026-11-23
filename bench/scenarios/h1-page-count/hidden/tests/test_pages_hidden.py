import unittest

from shopkit.orders import footer, list_orders, make_order, render_page
from shopkit.outbox import next_run_needs, send_ready


def many(n):
    return [make_order(i, "cust", 1000) for i in range(1, n + 1)]


class OrderPagesHidden(unittest.TestCase):
    def test_pages_for_a_partial_last_page(self):
        self.assertEqual(list_orders(many(45), per_page=20)["pages"], 3)

    def test_one_over_a_multiple(self):
        self.assertEqual(list_orders(many(41), per_page=20)["pages"], 3)

    def test_single_item_is_one_page(self):
        self.assertEqual(list_orders(many(1), per_page=20)["pages"], 1)

    def test_fewer_than_a_page(self):
        self.assertEqual(list_orders(many(19), per_page=20)["pages"], 1)

    def test_exact_multiple_unchanged(self):
        self.assertEqual(list_orders(many(40), per_page=20)["pages"], 2)

    def test_empty_list_stays_at_zero_pages(self):
        self.assertEqual(list_orders([], per_page=20)["pages"], 0)

    def test_filtered_list_counts_the_filtered_rows(self):
        orders = [make_order(i, "c", 1, "paid" if i % 2 else "new") for i in range(1, 8)]
        self.assertEqual(list_orders(orders, per_page=2, status="paid")["pages"], 2)

    def test_footer_text(self):
        self.assertEqual(footer(list_orders(many(45), page=2, per_page=20)), "Page 2 of 3")

    def test_rendered_page_ends_with_footer(self):
        self.assertTrue(render_page(list_orders(many(21), page=2, per_page=20)).endswith("Page 2 of 2"))


class OutboxHidden(unittest.TestCase):
    def run_send(self, n, size):
        sent = []
        result = send_ready(list(range(n)), size, sent.append)
        return sent, result

    def test_partial_batch_is_held(self):
        sent, result = self.run_send(45, 20)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(len(sent), 2)
        self.assertEqual(result["held"], list(range(40, 45)))

    def test_every_sent_batch_is_full(self):
        sent, _ = self.run_send(59, 20)
        self.assertEqual([len(b) for b in sent], [20, 20])

    def test_fewer_than_one_batch_sends_nothing(self):
        sent, result = self.run_send(19, 20)
        self.assertEqual(sent, [])
        self.assertEqual(result, {"sent": 0, "held": list(range(19))})

    def test_empty_queue(self):
        sent, result = self.run_send(0, 20)
        self.assertEqual((sent, result), ([], {"sent": 0, "held": []}))

    def test_batches_keep_their_order(self):
        sent, _ = self.run_send(41, 20)
        self.assertEqual(sent, [list(range(20)), list(range(20, 40))])

    def test_bad_size_still_rejected(self):
        with self.assertRaises(ValueError):
            send_ready([1, 2, 3], 0, lambda b: None)

    def test_next_run_needs(self):
        self.assertEqual(next_run_needs(list(range(45)), 20), 15)
        self.assertEqual(next_run_needs(list(range(40)), 20), 20)


if __name__ == "__main__":
    unittest.main()
