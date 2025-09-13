# yourapp/services/currency.py
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

def q2(x) -> Decimal:
    x = Decimal(x or 0)
    return x.quantize(CENT, rounding=ROUND_HALF_UP)

def to_cents(amount) -> int:
    return int(q2(amount) * 100)

def from_cents(cents: int) -> Decimal:
    return Decimal(int(cents)) / 100
