# Universal Video AI - Review Checklist

## Purpose
Systematic checklist for reviewing code changes. Use this checklist for every review.

## Pre-Review Checks
- [ ] Loaded PROJECT_BRAIN/04_CONSTITUTION.md
- [ ] Loaded PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- [ ] Loaded PROJECT_BRAIN/06_MODULE_MAP.md
- [ ] Loaded PROJECT_BRAIN/02_ROADMAP.md (DoD requirements)
- [ ] Loaded code being reviewed
- [ ] Understand the change context (milestone/commit)

## Constitution Compliance (04_CONSTITUTION.md)
- [ ] No print() statements
- [ ] No os.path usage (all pathlib)
- [ ] No global variables
- [ ] No staticmethod (unless justified)
- [ ] No bare except clauses
- [ ] No mutable default arguments
- [ ] Type hints 100% coverage
- [ ] All public functions have docstrings
- [ ] Logging used (not print)
- [ ] Dataclasses for data structures
- [ ] Protocols for interfaces
- [ ] Dependency injection used

## Dependency Graph Compliance (05_DEPENDENCY_GRAPH.md)
- [ ] No upward dependencies
- [ ] No circular dependencies
- [ ] No bypassing service layer
- [ ] Protocol-based dependencies
- [ ] Layer compliance verified
- [ ] No direct backend calls from adapters

## Module Permissions (06_MODULE_MAP.md)
- [ ] Frozen modules not modified
- [ ] Frozen interfaces not changed
- [ ] Frozen signatures not changed
- [ ] Mutable modifications allowed
- [ ] Protocol definitions unchanged
- [ ] Service signatures unchanged

## Public API Compliance (07_PUBLIC_API.md)
- [ ] Public API unchanged (unless major version)
- [ ] Backward compatible
- [ ] No breaking changes
- [ ] Deprecation process followed (if needed)

## Definition of Done (02_ROADMAP.md)
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] mypy passes (no type errors)
- [ ] ruff passes (no linting errors)
- [ ] Black formatted
- [ ] Logging added
- [ ] No TODO comments
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] CHANGELOG updated

## Code Quality
- [ ] Code is readable and clear
- [ ] Naming follows conventions
- [ ] Functions are small and focused
- [ ] Error handling is specific
- [ ] Resource management correct (context managers)
- [ ] Security best practices followed
- [ ] Performance considerations addressed
- [ ] No code duplication

## Testing Quality
- [ ] Tests follow naming conventions
- [ ] Tests use Arrange-Act-Assert
- [ ] Tests have descriptive names
- [ ] Tests use fixtures appropriately
- [ ] External dependencies mocked
- [ ] Edge cases tested
- [ ] Error conditions tested
- [ ] Test coverage adequate (>80%)

## Documentation
- [ ] Public API documented
- [ ] Complex logic commented
- [ ] Docstrings follow Google style
- [ ] CHANGELOG updated
- [ ] ADR created (if architectural change)

## Security
- [ ] User input validated
- [ ] Secrets not logged
- [ ] SQL injection prevented
- [ ] XSS prevention (if applicable)
- [ ] CSRF protection (if applicable)
- [ ] Authentication enforced (if applicable)

## Performance
- [ ] No obvious performance issues
- [ ] Efficient algorithms used
- [ ] No unnecessary computations
- [ ] Resource usage reasonable
- [ ] Caching used appropriately

## Error Handling
- [ ] Specific exceptions caught
- [ ] Error messages descriptive
- [ ] Errors logged appropriately
- [ ] Graceful degradation
- [ ] No silent failures

## Review Decision
Based on checklist, decide:
- **APPROVED**: All checklist items pass
- **REQUEST CHANGES**: Minor issues that need fixing
- **REJECTED**: Critical issues (frozen modules modified, breaking changes, security issues)

## Review Output Format
Provide review with:
1. Overall decision (APPROVED/REQUEST CHANGES/REJECTED)
2. Specific issues found (with file paths and line numbers)
3. Action items to fix
4. Reference to PROJECT_BRAIN documents

## Example Review Output
```
### Review Summary
REQUEST CHANGES

### Constitution Compliance
❌ src/universal_video_ai/speech/service.py:15 - print() instead of logging
❌ src/universal_video_ai/speech/service.py:23 - Missing type hint

### Dependency Graph Compliance
✅ No violations

### Module Permissions
✅ No violations

### DoD Compliance
❌ Missing unit tests
❌ CHANGELOG not updated

### Action Items
1. Replace print() with logging (line 15)
2. Add type hint (line 23)
3. Add unit tests
4. Update CHANGELOG.md
```

Use this checklist for every review to ensure consistency and quality.
