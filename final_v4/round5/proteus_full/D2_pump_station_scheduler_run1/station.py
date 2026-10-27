"""station -- pump-station orchestration: poll, schedule, command.

Combines all three transformations in one program:
  * LSA + GSA: swap the HTTP client (`requests` -> `httpx`) and write glue.
  * LMA: rewrite the first-party lead-lag staging logic in `scheduler.py`.
"""
from client import ScadaClient
from scheduler import PumpScheduler


class _CycleStages:
    """Encapsulates the sequential steps of a station polling cycle."""

    def __init__(self, client: ScadaClient, scheduler: PumpScheduler):
        self._client = client
        self._scheduler = scheduler

    def poll(self):
        demand = self._client.get_demand()
        runtimes = self._client.get_runtimes()
        return demand, runtimes

    def compute_plan(self, demand, runtimes) -> dict:
        return self._scheduler.select(demand, runtimes)

    def dispatch(self, plan: dict) -> None:
        for pump, on in plan.items():
            self._client.command(pump, on)

    def run(self) -> dict:
        demand, runtimes = self.poll()
        plan = self.compute_plan(demand, runtimes)
        self.dispatch(plan)
        return plan


class PumpStation:
    def __init__(self, client: ScadaClient, scheduler: PumpScheduler = None):
        self.client = client
        self.scheduler = scheduler if scheduler is not None else PumpScheduler()

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        return _CycleStages(self.client, self.scheduler).run()
