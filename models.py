"""Kontrakt reportu. Stejný tvar pro offline politiku i pro live model."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    PROPOSED_ACTION = "PROPOSED_ACTION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class ApprovalState(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED_READ_ONLY"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    BLOCKED_GUARDRAIL = "BLOCKED_BY_GUARDRAIL"
    AWAITING = "AWAITING_APPROVAL"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditStep(BaseModel):
    step_number: int
    timestamp: str = Field(default_factory=utc_now)
    thought: str
    tool_called: str
    tool_args: dict[str, Any]
    tool_output: dict[str, Any]
    approval_status: ApprovalState
    error: str | None = None


class FinalLLMDecision(BaseModel):
    """Závěr, který smí vrátit model. Brána ho smí přepsat, když fakta nesedí."""

    status: ExecutionStatus
    root_cause: str
    recommended_next_steps: list[str]
    summary_for_operator: str
    sop_reference: str
    customer_care_suggestion: str


class AgentExecutionReport(BaseModel):
    request: str
    final_status: ExecutionStatus
    root_cause_summary: str
    proposed_next_steps: list[str]
    summary_for_operator: str
    sop_reference: str
    customer_care_suggestion: str
    audit_trail: list[AuditStep]

    @classmethod
    def from_decision(
        cls,
        request: str,
        decision: FinalLLMDecision,
        audit_trail: list[AuditStep],
    ) -> "AgentExecutionReport":
        return cls(
            request=request,
            final_status=decision.status,
            root_cause_summary=decision.root_cause,
            proposed_next_steps=decision.recommended_next_steps,
            summary_for_operator=decision.summary_for_operator,
            sop_reference=decision.sop_reference,
            customer_care_suggestion=decision.customer_care_suggestion,
            audit_trail=audit_trail,
        )
