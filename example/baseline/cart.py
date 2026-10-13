"""Tiny shopping cart used by the example. The BASELINE has two bugs."""


def total(prices, discount_percent=0):
    subtotal = sum(prices)
    # bug 1: discount is not capped, so 150% yields a negative total
    discounted = subtotal * (100 - discount_percent) / 100
    return round(discounted, 2)


def cheapest(prices):
    # bug 2: an empty cart raises instead of returning None
    return min(prices)
