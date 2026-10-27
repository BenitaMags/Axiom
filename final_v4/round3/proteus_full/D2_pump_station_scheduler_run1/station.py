"""station -- pump-station orchestration: poll, schedule, command.

Combines all three transformations in one program:
  * LSA + GSA: swap the HTTP client (`requests` -> `httpx`) and write glue.
  * LMA: rewrite the first-party lead-lag staging logic in `scheduler.py`.
"""
from client import ScadaClient
from scheduler import PumpScheduler


class _CyclePipeline:
    """Runs the poll -> schedule -> command sequence for a station."""

    def __init__(self, client: ScadaClient, scheduler: PumpScheduler):
        self._client = client
        self._scheduler = scheduler

    def run(self) -> dict:
        demand = self._client.get_demand()
        runtimes = self._client.get_runtimes()
        plan = self._scheduler.select(demand, runtimes)
        self._apply(plan)
        return plan

    def _apply(self, plan: dict) -> None:
        for pump, on in plan.items():
            self._client.command(pump, on)


class PumpStation:
    def __init__(self, client: ScadaClient, scheduler: PumpScheduler = None):
        self.client = client
        self.scheduler = scheduler if scheduler is not None else PumpScheduler()

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        return _CyclePipeline(self.client, self.scheduler).run()
