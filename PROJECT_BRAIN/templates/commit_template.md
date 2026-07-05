# Commit Message Template

## Format
```
[Milestone X] Commit Y: Brief description

## Changes
- File 1: Description of change
- File 2: Description of change

## Rationale
Why this change was made.

## Testing
- Unit tests: test_file.py
- Integration tests: test_integration.py

## DoD Verification
- [x] Unit tests pass
- [x] Integration tests pass
- [x] mypy passes
- [x] ruff passes
- [x] Black formatted
- [x] Logging added
- [x] No TODO
- [x] Type hints 100%
- [x] Backward compatible
- [x] CHANGELOG updated

## References
- Milestone: PROJECT_BRAIN/02_ROADMAP.md
- ADR: PROJECT_BRAIN/03_DECISIONS.md (if applicable)
```

## Example
```
[Milestone 1] Commit 24: LocalizationService Factory

## Changes
- src/universal_video_ai/orchestrator/factory.py: Created factory function
- tests/test_orchestrator_factory.py: Added factory tests

## Rationale
Need centralized factory to create LocalizationService with configurable backends per ADR-001.

## Testing
- Unit tests: test_orchestrator_factory.py

## DoD Verification
- [x] Unit tests pass
- [x] mypy passes
- [x] ruff passes
- [x] Black formatted
- [x] Logging added
- [x] No TODO
- [x] Type hints 100%
- [x] Backward compatible
- [x] CHANGELOG updated

## References
- Milestone: PROJECT_BRAIN/02_ROADMAP.md (Commit 24)
- ADR: PROJECT_BRAIN/03_DECISIONS.md (ADR-001)
```
