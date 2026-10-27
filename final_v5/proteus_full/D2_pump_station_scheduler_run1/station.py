"""station -- pump-station orchestration: poll, schedule, command.

Combines all three transformations in one program:
  * LSA + GSA: swap the HTTP client (`requests` -> `httpx`) and write glue.
  * LMA: rewrite the first-party lead-lag staging logic in `scheduler.py`.
"""
from client import ScadaClient
from scheduler import PumpScheduler


class PumpStation:
    def __init__(self, client: ScadaClient, scheduler: PumpScheduler = None):
        self.client = client
        if not (scheduler is not None):
            self.scheduler = PumpScheduler()
        else:
            self.scheduler = scheduler

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        demand = self.client.get_demand()
        runtimes = self.client.get_runtimes()
        plan = self.scheduler.select(demand, runtimes)
        for pump, on in plan.items():
            self.client.command(pump, on)
        return plan
