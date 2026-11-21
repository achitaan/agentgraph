"""Sliding-window helpers."""


def moving_sum(values, size):
    """Sums of every run of `size` consecutive values, in order.

    moving_sum([1, 2, 3, 4], 2) == [3, 5, 7]
    A window larger than the list gives an empty list.
    """
    if size < 1:
        raise ValueError("size must be at least 1")
    sums = []
    for start in range(len(values) - size):
        sums.append(sum(values[start:start + size]))
    return sums
