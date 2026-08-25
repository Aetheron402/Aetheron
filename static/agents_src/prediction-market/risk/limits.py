from datetime import datetime
from typing import List

from schemas import Order


class LimitManager:
    def __init__(self, max_orders_per_run: int):
        self.max_orders_per_run = max_orders_per_run

    def allow(self, orders_this_run: List[Order]) -> bool:
        return len(orders_this_run) < self.max_orders_per_run