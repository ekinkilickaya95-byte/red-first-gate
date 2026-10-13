"""Tiny shopping cart used by the example. The CANDIDATE fixes both bugs."""


def total(prices, discount_percent=0):
    subtotal = sum(prices)
    capped = min(max(discount_percent, 0), 100)
    discounted = subtotal * (100 - capped) / 100
    return round(discounted, 2)


def cheapest(prices):
    if not prices:
        return None
    return min(prices)
