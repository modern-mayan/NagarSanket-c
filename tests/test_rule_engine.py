from backend.civicpulse_core import (
    CivicTicket,
    EscalationLevel,
    HazardType,
    Severity,
    apply_rule_engine,
)


def _base_ticket(hazard: HazardType, severity: Severity, escalation: EscalationLevel) -> CivicTicket:
    return CivicTicket(
        incident_title="Hazard report",
        hazard_type=hazard,
        severity=severity,
        confidence=0.81,
        visible_evidence=["Visible risk in lane"],
        public_risk_summary="Risk for pedestrians.",
        immediate_citizen_action="Keep distance and alert neighbors.",
        responsible_department="Municipal Control Room",
        ticket_description="Auto-generated ticket.",
        location_text="DLF Phase 3",
        escalation_level=escalation,
    )


def test_fallen_wire_forces_critical_and_emergency() -> None:
    ticket = _base_ticket(HazardType.FALLEN_WIRE, Severity.LOW, EscalationLevel.ROUTINE)
    updated = apply_rule_engine(ticket, "wire has fallen near road")
    assert updated.severity == Severity.CRITICAL
    assert updated.escalation_level == EscalationLevel.EMERGENCY


def test_open_manhole_minimum_high() -> None:
    ticket = _base_ticket(HazardType.OPEN_MANHOLE, Severity.MEDIUM, EscalationLevel.ROUTINE)
    updated = apply_rule_engine(ticket, "open pit on street")
    assert updated.severity == Severity.HIGH
    assert updated.escalation_level == EscalationLevel.URGENT


def test_sensitive_keyword_bumps_once() -> None:
    ticket = _base_ticket(HazardType.WATERLOGGING, Severity.MEDIUM, EscalationLevel.ROUTINE)
    updated = apply_rule_engine(ticket, "waterlogging near school gate")
    assert updated.severity == Severity.HIGH
    assert updated.escalation_level == EscalationLevel.URGENT
