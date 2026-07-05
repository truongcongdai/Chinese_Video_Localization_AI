# Universal Video AI - Self Review Checklist

## Purpose
Self-review checklist for AI to verify code before outputting. Use this checklist after writing code.

## Pre-Output Check
Before providing code to user, verify:

### Constitution Check
- [ ] No print() statements → use logging
- [ ] No os.path → use pathlib
- [ ] No global variables → use DI
- [ ] No staticmethod → use functions/instance methods
- [ ] No bare except → catch specific exceptions
- [ ] No mutable defaults → use None with default
- [ ] Type hints 100% → add missing hints
- [ ] Docstrings present → add for public functions

### Dependency Check
- [ ] No upward dependencies → check 05_DEPENDENCY_GRAPH.md
- [ ] No circular dependencies → verify dependency flow
- [ ] No bypassing service layer → use protocols
- [ ] Protocol-based → use protocols, not concrete implementations

### Module Permission Check
- [ ] Frozen modules not modified → check 06_MODULE_MAP.md
- [ ] Frozen interfaces unchanged → verify protocol definitions
- [ ] Service signatures unchanged → verify public methods

### DoD Check
- [ ] Unit tests included → write tests first
- [ ] mypy will pass → verify type hints
- [ ] ruff will pass → verify code style
- [ ] Black formatted → verify formatting
- [ ] Logging added → use logger
- [ ] No TODO → remove TODO comments
- [ ] Type hints 100% → add missing hints
- [ ] Backward compatible → verify no breaking changes
- [ ] CHANGELOG updated → mention in output

## Code Quality Check
- [ ] Code is readable → use clear names
- [ ] Functions are small → split if too long
- [ ] Error handling specific → catch specific exceptions
- [ ] Resources managed → use context managers
- [ ] No duplication → extract common logic

## Common Issues to Fix

### Issue: print() Statement
**Problem**: Code contains print()
**Fix**: Replace with logger.debug/info/warning/error

### Issue: os.path Usage
**Problem**: Code uses os.path
**Fix**: Replace with pathlib.Path

### Issue: Missing Type Hint
**Problem**: Function missing type hint
**Fix**: Add type hint for parameter and return

### Issue: Global Variable
**Problem**: Code uses global variable
**Fix**: Use dependency injection instead

### Issue: Bare Except
**Problem**: except: without exception type
**Fix**: except SpecificError as exc:

### Issue: Mutable Default
**Problem**: def func(items=[]):
**Fix**: def func(items: Optional[List] = None):

## Self-Review Process

### Step 1: Constitution Review
Scan code for:
- print() → replace with logging
- os.path → replace with pathlib
- global variables → use DI
- staticmethod → use functions
- bare except → specific exceptions
- mutable defaults → use None with default

### Step 2: Dependency Review
Check:
- Are dependencies downward only?
- Are protocols used for cross-module dependencies?
- Is service layer not bypassed?

### Step 3: Permission Review
Check:
- Are frozen modules not modified?
- Are frozen interfaces unchanged?
- Are service signatures unchanged?

### Step 4: DoD Review
Check:
- Are tests included?
- Are type hints complete?
- Is logging used?
- Are TODOs removed?
- Is CHANGELOG mentioned?

### Step 5: Quality Review
Check:
- Is code readable?
- Are functions small?
- Is error handling specific?
- Are resources managed?

## Self-Correction Examples

### Example 1: print() Found
**Before**:
```python
print(f"Processing {url}")
```

**After**:
```python
logger.info("Processing %s", url)
```

### Example 2: os.path Found
**Before**:
```python
import os
path = os.path.join("/tmp", "file.txt")
```

**After**:
```python
from pathlib import Path
path = Path("/tmp") / "file.txt"
```

### Example 3: Missing Type Hint
**Before**:
```python
def process(data):
    return data * 2
```

**After**:
```python
def process(data: str) -> str:
    return data * 2
```

### Example 4: Bare Except
**Before**:
```python
try:
    risky()
except:
    pass
```

**After**:
```python
try:
    risky()
except SpecificError as exc:
    logger.warning("Operation failed: %s", exc)
```

## Final Verification
Before outputting code, ask:
1. Does this follow 04_CONSTITUTION.md?
2. Does this follow 05_DEPENDENCY_GRAPH.md?
3. Does this follow 06_MODULE_MAP.md?
4. Does this meet DoD from 02_ROADMAP.md?
5. Are tests included?
6. Is CHANGELOG mentioned?

If answer is YES to all, output code.
If answer is NO to any, fix the issue first.

## Output Format
When providing code, include:
1. Context summary
2. Files modified
3. Implementation
4. DoD verification checklist

Use this self-review checklist for every code output to ensure quality.
