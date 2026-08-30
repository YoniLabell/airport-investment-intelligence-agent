"""The AI layer.

Claude orchestrates and explains. It never computes a statistic: every figure it
mentions must have come back from one of the deterministic tools in
:mod:`app.agent.tools`.
"""

from app.agent.service import AgentService, get_agent_service

__all__ = ["AgentService", "get_agent_service"]
