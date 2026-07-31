"""
Test Content OS state machine.

Verifies workflow state transitions are validated correctly.
"""
import pytest

from universal_video_ai.content_os.state_machine import StateMachine
from universal_video_ai.content_os.enums import WorkflowStage
from universal_video_ai.content_os.exceptions import InvalidTransitionError


class TestStateMachine:
    """Test state machine transition validation."""
    
    def test_valid_transition_created_to_trend_research(self):
        """Test valid transition from CREATED to TREND_RESEARCH."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.CREATED, WorkflowStage.TREND_RESEARCH)
    
    def test_valid_transition_trend_research_to_source_selection(self):
        """Test valid transition from TREND_RESEARCH to SOURCE_SELECTION."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.TREND_RESEARCH, WorkflowStage.SOURCE_SELECTION)
    
    def test_valid_transition_script_audit_to_awaiting_approval(self):
        """Test valid transition from SCRIPT_AUDIT to AWAITING_APPROVAL."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.SCRIPT_AUDIT, WorkflowStage.AWAITING_APPROVAL)
    
    def test_valid_transition_script_audit_to_script_revision(self):
        """Test valid transition from SCRIPT_AUDIT to SCRIPT_REVISION."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.SCRIPT_AUDIT, WorkflowStage.SCRIPT_REVISION)
    
    def test_valid_transition_awaiting_approval_to_approved(self):
        """Test valid transition from AWAITING_APPROVAL to APPROVED."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.AWAITING_APPROVAL, WorkflowStage.APPROVED, has_approval=True)
    
    def test_invalid_transition_skip_stage(self):
        """Test invalid transition that skips a stage."""
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition(WorkflowStage.CREATED, WorkflowStage.SCRIPT_WRITING)
    
    def test_invalid_transition_backwards(self):
        """Test invalid backwards transition."""
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition(WorkflowStage.SCRIPT_WRITING, WorkflowStage.TREND_RESEARCH)
    
    def test_valid_transition_to_cancelled(self):
        """Test valid transition to CANCELLED from any stage."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.TREND_RESEARCH, WorkflowStage.CANCELLED)
        sm.validate_transition(WorkflowStage.SCRIPT_WRITING, WorkflowStage.CANCELLED)
        sm.validate_transition(WorkflowStage.AWAITING_APPROVAL, WorkflowStage.CANCELLED, has_approval=True)
    
    def test_valid_transition_to_paused(self):
        """Test valid transition to PAUSED from appropriate stages."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.TREND_RESEARCH, WorkflowStage.PAUSED)
        sm.validate_transition(WorkflowStage.SCRIPT_WRITING, WorkflowStage.PAUSED)
        sm.validate_transition(WorkflowStage.LOCALIZATION_RUNNING, WorkflowStage.PAUSED)
    
    def test_invalid_transition_from_completed(self):
        """Test that COMPLETED is a terminal state."""
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition(WorkflowStage.COMPLETED, WorkflowStage.TREND_RESEARCH)
    
    def test_invalid_transition_to_created(self):
        """Test that you cannot transition back to CREATED."""
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition(WorkflowStage.TREND_RESEARCH, WorkflowStage.CREATED)
    
    def test_revision_loop(self):
        """Test the revision loop: AUDITING → REVISING → AUDITING."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.SCRIPT_AUDITING, WorkflowStage.SCRIPT_REVISING)
        sm.validate_transition(WorkflowStage.SCRIPT_REVISING, WorkflowStage.SCRIPT_AUDITING)
    
    def test_full_workflow_path(self):
        """Test the complete happy path workflow."""
        sm = StateMachine()
        transitions = [
            (WorkflowStage.CREATED, WorkflowStage.RESEARCHING),
            (WorkflowStage.RESEARCHING, WorkflowStage.RESEARCH_READY),
            (WorkflowStage.RESEARCH_READY, WorkflowStage.CONTENT_PLANNING),
            (WorkflowStage.CONTENT_PLANNING, WorkflowStage.PLAN_READY),
            (WorkflowStage.PLAN_READY, WorkflowStage.SCRIPT_WRITING),
            (WorkflowStage.SCRIPT_WRITING, WorkflowStage.SCRIPT_AUDITING),
            (WorkflowStage.SCRIPT_AUDITING, WorkflowStage.AWAITING_SCRIPT_APPROVAL),
            (WorkflowStage.AWAITING_SCRIPT_APPROVAL, WorkflowStage.ASSET_PLANNING, True),
            (WorkflowStage.ASSET_PLANNING, WorkflowStage.ASSET_RESOLVING),
            (WorkflowStage.ASSET_RESOLVING, WorkflowStage.ASSETS_READY),
            (WorkflowStage.ASSETS_READY, WorkflowStage.VOICE_GENERATION),
            (WorkflowStage.VOICE_GENERATION, WorkflowStage.SUBTITLE_GENERATION),
            (WorkflowStage.SUBTITLE_GENERATION, WorkflowStage.TIMELINE_BUILDING),
            (WorkflowStage.TIMELINE_BUILDING, WorkflowStage.RENDERING),
            (WorkflowStage.RENDERING, WorkflowStage.OUTPUT_VALIDATION),
            (WorkflowStage.OUTPUT_VALIDATION, WorkflowStage.COMPLETED),
        ]
        
        for transition in transitions:
            if len(transition) == 3:  # (from_stage, to_stage, has_approval)
                from_stage, to_stage, has_approval = transition
                sm.validate_transition(from_stage, to_stage, has_approval=has_approval)
            else:  # (from_stage, to_stage)
                from_stage, to_stage = transition
                sm.validate_transition(from_stage, to_stage)
    
    def test_blocked_transition(self):
        """Test transition to BLOCKED state."""
        sm = StateMachine()
        sm.validate_transition(WorkflowStage.SCRIPT_AUDIT, WorkflowStage.BLOCKED)
        sm.validate_transition(WorkflowStage.AWAITING_APPROVAL, WorkflowStage.BLOCKED, has_approval=True)
