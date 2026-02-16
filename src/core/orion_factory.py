"""
ORION FACTORY - Spawn Infinite ORIONs
The system that creates Project Managers on demand

Philosophy: Why have ONE PM when you can have INFINITE PMs?
Each ORION is a specialized instance managing its own domain.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class OrionDomain(Enum):
    """Domains that ORIONs can specialize in"""
    CODE = "code"              # Code generation & refactoring
    UI_UX = "ui_ux"            # UI/UX improvements
    TESTING = "testing"        # QA & testing
    DEPLOYMENT = "deployment"  # DevOps & deployment
    SECURITY = "security"      # Security audits
    RESEARCH = "research"      # Research & analysis
    PROJECT = "project"        # Full project management
    MONITORING = "monitoring"  # System monitoring


@dataclass
class OrionInstance:
    """A single ORION instance with its own context and responsibilities"""
    id: str
    name: str
    domain: OrionDomain
    status: str = "idle"
    created_at: datetime = field(default_factory=datetime.now)
    current_task: Optional[str] = None
    assigned_agents: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    decisions_made: int = 0
    tasks_completed: int = 0

    def __post_init__(self):
        self.name = f"ORION-{self.domain.value.upper()}-{self.id[:8]}"


class OrionFactory:
    """
    Factory that spawns and manages ORION instances

    Capabilities:
    - Spawn new ORIONs on demand
    - Route tasks to appropriate ORION
    - ORION-to-ORION communication
    - Auto-scale based on workload
    - Full autonomy mode
    """

    def __init__(self, max_orions: int = 10, autonomy_level: str = "full"):
        self.max_orions = max_orions
        self.autonomy_level = autonomy_level  # "full", "semi", "manual"
        self.instances: Dict[str, OrionInstance] = {}
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.is_running = False

        # Domain to agent mapping
        self.domain_agents = {
            OrionDomain.CODE: ["NOVA", "CIPHER"],
            OrionDomain.UI_UX: ["PIXEL"],
            OrionDomain.TESTING: ["ECHO"],
            OrionDomain.DEPLOYMENT: ["FLUX"],
            OrionDomain.SECURITY: ["CIPHER"],
            OrionDomain.RESEARCH: ["ORION"],
            OrionDomain.PROJECT: ["NOVA", "PIXEL", "ECHO", "FLUX", "CIPHER"],
            OrionDomain.MONITORING: ["GUARDIAN"]
        }

        # Spawn initial ORIONs
        self._spawn_initial_orions()

    def _spawn_initial_orions(self):
        """Spawn essential ORIONs at startup"""
        essential = [
            OrionDomain.PROJECT,    # Main project ORION
            OrionDomain.CODE,       # Code ORION
            OrionDomain.UI_UX,      # UI/UX ORION
        ]

        for domain in essential:
            self.spawn_orion(domain)

        logger.info(f"Spawned {len(essential)} initial ORIONs")

    def spawn_orion(self, domain: OrionDomain, context: Dict = None) -> OrionInstance:
        """Spawn a new ORION instance for a specific domain"""
        if len(self.instances) >= self.max_orions:
            # Find idle ORION and repurpose
            for orion in self.instances.values():
                if orion.status == "idle":
                    logger.info(f"Repurposing {orion.name} for {domain.value}")
                    orion.domain = domain
                    orion.name = f"ORION-{domain.value.upper()}-{orion.id[:8]}"
                    return orion
            raise RuntimeError(f"Max ORIONs reached ({self.max_orions})")

        orion_id = str(uuid.uuid4())
        orion = OrionInstance(
            id=orion_id,
            name="",  # Set in __post_init__
            domain=domain,
            context=context or {},
            assigned_agents=self.domain_agents.get(domain, [])
        )

        self.instances[orion_id] = orion
        logger.info(f"Spawned new ORION: {orion.name} for domain: {domain.value}")

        return orion

    def get_orion_for_task(self, task_type: str) -> Optional[OrionInstance]:
        """Get the best ORION for a specific task type"""
        domain_mapping = {
            "code": OrionDomain.CODE,
            "refactor": OrionDomain.CODE,
            "ui": OrionDomain.UI_UX,
            "ux": OrionDomain.UI_UX,
            "design": OrionDomain.UI_UX,
            "test": OrionDomain.TESTING,
            "qa": OrionDomain.TESTING,
            "deploy": OrionDomain.DEPLOYMENT,
            "security": OrionDomain.SECURITY,
            "research": OrionDomain.RESEARCH,
            "project": OrionDomain.PROJECT,
            "monitor": OrionDomain.MONITORING,
        }

        domain = domain_mapping.get(task_type.lower(), OrionDomain.PROJECT)

        # Find existing ORION for this domain
        for orion in self.instances.values():
            if orion.domain == domain:
                return orion

        # Spawn new ORION if needed (FULL AUTONOMY)
        if self.autonomy_level == "full":
            return self.spawn_orion(domain)

        return None

    async def delegate_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Delegate a task to the appropriate ORION"""
        task_type = task.get("type", "project")

        orion = self.get_orion_for_task(task_type)
        if not orion:
            return {"error": "No ORION available", "task": task}

        orion.status = "working"
        orion.current_task = task.get("description", "Unknown task")

        logger.info(f"{orion.name} assigned task: {orion.current_task}")

        # Execute task through ORION
        result = await self._execute_with_orion(orion, task)

        orion.status = "idle"
        orion.current_task = None
        orion.tasks_completed += 1

        return result

    async def _execute_with_orion(self, orion: OrionInstance, task: Dict) -> Dict:
        """Execute a task using ORION's assigned agents"""
        # This will integrate with the actual agent system
        result = {
            "orion": orion.name,
            "task": task,
            "status": "completed",
            "agents_used": orion.assigned_agents,
            "timestamp": datetime.now().isoformat()
        }

        orion.decisions_made += 1

        return result

    def get_status(self) -> Dict:
        """Get factory status"""
        return {
            "total_orions": len(self.instances),
            "max_orions": self.max_orions,
            "autonomy_level": self.autonomy_level,
            "orions": [
                {
                    "id": o.id,
                    "name": o.name,
                    "domain": o.domain.value,
                    "status": o.status,
                    "current_task": o.current_task,
                    "tasks_completed": o.tasks_completed,
                    "decisions_made": o.decisions_made,
                    "assigned_agents": o.assigned_agents
                }
                for o in self.instances.values()
            ]
        }

    def broadcast_to_orions(self, message: str, exclude: str = None):
        """Broadcast a message to all ORIONs"""
        for orion_id, orion in self.instances.items():
            if exclude and orion_id == exclude:
                continue
            # In full implementation, this would send to ORION's message queue
            logger.info(f"Broadcast to {orion.name}: {message}")

    async def run_autonomous_loop(self):
        """
        Main autonomous loop - runs 24/7 without human intervention

        This is the HEART of the full automation system
        """
        self.is_running = True
        logger.info("ORION FACTORY - Autonomous Mode ACTIVATED")
        logger.info(f"Running {len(self.instances)} ORIONs")

        while self.is_running:
            try:
                # Process task queue
                if not self.task_queue.empty():
                    task = await self.task_queue.get()
                    await self.delegate_task(task)

                # Auto-scale: spawn more ORIONs if needed
                await self._auto_scale()

                # Health check all ORIONs
                await self._health_check()

                await asyncio.sleep(1)  # Prevent CPU spinning

            except Exception as e:
                logger.error(f"Autonomous loop error: {e}")
                await asyncio.sleep(5)

    async def _auto_scale(self):
        """Auto-scale ORIONs based on workload"""
        working_count = sum(1 for o in self.instances.values() if o.status == "working")
        idle_count = sum(1 for o in self.instances.values() if o.status == "idle")

        # If all ORIONs are working, spawn more (up to max)
        if working_count > 0 and idle_count == 0 and len(self.instances) < self.max_orions:
            # Determine which domain needs more ORIONs
            domains_in_use = [o.domain for o in self.instances.values() if o.status == "working"]
            if domains_in_use:
                most_busy = max(set(domains_in_use), key=domains_in_use.count)
                logger.info(f"Auto-scaling: Spawning new ORION for {most_busy.value}")
                self.spawn_orion(most_busy)

    async def _health_check(self):
        """Health check all ORION instances"""
        for orion in self.instances.values():
            # Reset stuck ORIONs (working for more than 5 minutes)
            if orion.status == "working":
                # In full implementation, check timestamps
                pass

    def shutdown(self):
        """Gracefully shutdown all ORIONs"""
        self.is_running = False
        logger.info("ORION FACTORY - Shutting down...")
        for orion in self.instances.values():
            logger.info(f"  {orion.name}: {orion.tasks_completed} tasks completed")


# Global factory instance
_orion_factory: Optional[OrionFactory] = None


def get_orion_factory(autonomy_level: str = "full") -> OrionFactory:
    """Get or create the global ORION factory"""
    global _orion_factory
    if _orion_factory is None:
        _orion_factory = OrionFactory(autonomy_level=autonomy_level)
    return _orion_factory


# ═══════════════════════════════════════════════════════════════
# DEMO: Show the power of multiple ORIONs
# ═══════════════════════════════════════════════════════════════

async def demo_orion_swarm():
    """Demo: Multiple ORIONs working in parallel"""
    factory = get_orion_factory(autonomy_level="full")

    print("\n" + "="*60)
    print("ORION SWARM DEMO")
    print("="*60)

    # Show initial ORIONs
    status = factory.get_status()
    print(f"\nInitial ORIONs: {status['total_orions']}")
    for orion in status['orions']:
        print(f"  - {orion['name']}: {orion['domain']} ({orion['assigned_agents']})")

    # Simulate tasks being delegated
    tasks = [
        {"type": "code", "description": "Refactor API endpoints"},
        {"type": "ui", "description": "Improve button styles"},
        {"type": "test", "description": "Add integration tests"},
        {"type": "code", "description": "Fix memory leak"},
        {"type": "deploy", "description": "Deploy to production"},
    ]

    print("\n" + "-"*60)
    print("DELEGATING TASKS TO ORION SWARM")
    print("-"*60)

    for task in tasks:
        result = await factory.delegate_task(task)
        print(f"  {result['orion']} → {result['task']['description']}")

    # Final status
    status = factory.get_status()
    print("\n" + "-"*60)
    print("FINAL STATUS")
    print("-"*60)
    print(f"Total ORIONs: {status['total_orions']}")
    for orion in status['orions']:
        print(f"  - {orion['name']}: {orion['tasks_completed']} tasks, {orion['decisions_made']} decisions")


if __name__ == "__main__":
    asyncio.run(demo_orion_swarm())
