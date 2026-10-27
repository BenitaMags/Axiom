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
        self.scheduler = scheduler or PumpScheduler()

    def _fetch_state(self):
        current_demand = self.client.get_demand()
        current_runtimes = self.client.get_runtimes()
        return current_demand, current_runtimes

    def _apply_plan(self, plan: dict) -> None:
        for pump_idx, should_run in plan.items():
            self.client.command(pump_idx, should_run)

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        demand, runtimes = self._fetch_state()
        staging_plan = self.scheduler.select(demand, runtimes)
        self._apply_plan(staging_plan)
        return staging_plan
