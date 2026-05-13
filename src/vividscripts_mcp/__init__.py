"""VividScripts MCP server — remote MCP for AI-driven story-to-video workflows."""

from vividscripts_mcp.models import (
    JobStatus,
    ProjectDetail,
    ProjectInfo,
    ProjectSummary,
    PromptPayload,
    Scene,
    StepDefinition,
    StepResultOutcome,
    WorkflowState,
)

__version__ = "0.1.0a0"

__all__ = [
    "JobStatus",
    "ProjectDetail",
    "ProjectInfo",
    "ProjectSummary",
    "PromptPayload",
    "Scene",
    "StepDefinition",
    "StepResultOutcome",
    "WorkflowState",
    "__version__",
]
