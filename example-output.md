## anchors: GREEN

## known-bad: GREEN
- baseline: 2/4 green · candidate: 4/4 green
| new test | baseline | candidate | evidence |
|---|---|---|---|
| test_total_discount_capped | fail | pass | catches old behaviour |
| test_cheapest_empty_is_none | fail | pass | catches old behaviour |
| test_cheapest_picks_minimum | pass | pass | needs a killer |

## mutants: GREEN
| mutant | killed | expected tests | other red tests |
|---|---|---|---|
| m1-discount-not-capped | yes | test_total_discount_capped=fail | — |
| m2-empty-cart-raises | yes | test_cheapest_empty_is_none=fail | — |
| m3-cheapest-returns-max | yes | test_cheapest_picks_minimum=fail | — |

## killers: GREEN
| green-on-both test | killed by |
|---|---|
| test_cheapest_picks_minimum | m3-cheapest-returns-max |

