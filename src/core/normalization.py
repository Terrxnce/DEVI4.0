from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    scaled = Decimal(str(price)) / Decimal(str(tick_size))
    rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(rounded * Decimal(str(tick_size)))


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    scaled = Decimal(str(value)) / Decimal(str(step))
    rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(rounded * Decimal(str(step)))
