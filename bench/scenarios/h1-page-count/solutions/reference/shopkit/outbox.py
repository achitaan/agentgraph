"""The outbox: queued messages go to the carrier in batches of a fixed size.

The carrier charges per batch and only accepts full ones, so a run sends every full batch and holds back the
leftover messages in the queue until enough have arrived to fill one.
"""
from .paging import check_size, page_slice


def send_ready(queue, size, send):
    """Send each full batch of `size` messages through `send(batch)`.

    Returns {"sent": number of batches sent, "held": the leftover messages, in order}.
    """
    check_size(size)
    batches = len(queue) // size
    for number in range(1, batches + 1):
        send(page_slice(queue, number, size))
    return {"sent": batches, "held": list(queue[batches * size:])}


def next_run_needs(queue, size):
    """How many more messages must arrive before the next batch can go out."""
    if len(queue) % size == 0 and queue:
        return size
    return size - len(queue) % size
