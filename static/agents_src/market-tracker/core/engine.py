from datetime import datetime
from typing import Dict, Callable

from schemas import AgentOutput, ModuleResult

class MarketEngine:
    def __init__(self, modules: Dict[str, Callable], config: dict):
        self.modules = modules
        self.config = config

    def run(self) -> Dict[str, ModuleResult]:
        results: Dict[str, ModuleResult] = {}

        for name, module in self.modules.items():
            results[name] = module(self.config.get("modules", {}).get(name, {}))

        return results

    def timestamp(self) -> str:
        return datetime.utcnow().isoformat()
