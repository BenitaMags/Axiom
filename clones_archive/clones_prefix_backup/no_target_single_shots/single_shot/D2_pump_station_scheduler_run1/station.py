"""station -- pump-station orchestration: poll, schedule, command.

Combines all three transformations in one program:

  * LMA: rewrite the first-party lead-lag staging logic in `scheduler.py`.
"""
from client import ScadaClient
from scheduler import PumpScheduler


class PumpStation:
    def __init__(self, client: ScadaClient, scheduler: PumpScheduler = None):
        self.client = client
        self.scheduler = scheduler if scheduler is not None else PumpScheduler()

    def cycle(self) -> dict:
        """Poll demand/runtimes, compute the staging plan, and issue commands."""
        current_demand = self.client.get_demand()
        pump_runtimes = self.client.get_runtimes()
        staging_plan = self.scheduler.select(current_demand, pump_runtimes)

        for pump_id, should_run in staging_plan.items():
            self.client.command(pump_id, should_run)

        return staging_plan
