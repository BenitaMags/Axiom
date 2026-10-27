"""station -- pump-station orchestration: poll, schedule, command.

Combines all three transformations in one program:
  * LSA + GSA: swap the HTTP client (`requests` -> `httpx`) and write glue.
  * LMA: rewrite the first-party lead-lag staging logic in `scheduler.py`.
"""
from client import ScadaClient
from scheduler import PumpScheduler


def _default_scheduler():
    return PumpScheduler()


class PumpStation:
    def __init__(self, client: ScadaClient, scheduler: PumpScheduler = None):
        self.client = client
        self.scheduler = scheduler if scheduler is not None else _default_scheduler()

    def _poll(self):
        return self.client.get_demand(), self.client.get_runtimes()

    def _issue(self, plan: dict) -> dict:
        for pump, on in plan.items():
            self.client.command(pump, on)
        return plan

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        demand, runtimes = self._poll()
        plan = self.scheduler.select(demand, runtimes)
        return self._issue(plan)
