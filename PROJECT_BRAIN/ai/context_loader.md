# Universal Video AI - Context Loader

## Purpose
This document defines which PROJECT_BRAIN files to load for different scenarios to optimize token usage while ensuring AI has necessary context.

## Universal Context (Always Load)
These files should be loaded for ANY AI session:
1. PROJECT_BRAIN/01_ARCHITECTURE.md
2. PROJECT_BRAIN/04_CONSTITUTION.md
3. PROJECT_BRAIN/03_DECISIONS.md
4. PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
5. PROJECT_BRAIN/06_MODULE_MAP.md
6. PROJECT_BRAIN/07_PUBLIC_API.md
7. PROJECT_BRAIN/08_TESTING_GUIDE.md
8. PROJECT_BRAIN/09_AI_RULES.md

## Role-Specific Context

### Architect
Load universal context plus:
- PROJECT_BRAIN/02_ROADMAP.md (current milestone)
- PROJECT_BRAIN/prompts/architect.md

### Implementer
Load universal context plus:
- PROJECT_BRAIN/02_ROADMAP.md (current milestone/commit)
- PROJECT_BRAIN/prompts/implementer.md
- Architect's design (if provided)

### Reviewer
Load universal context plus:
- PROJECT_BRAIN/02_ROADMAP.md (current milestone/commit)
- PROJECT_BRAIN/prompts/reviewer.md
- PROJECT_BRAIN/ai/review_checklist.md
- Code being reviewed

### Tester
Load universal context plus:
- PROJECT_BRAIN/02_ROADMAP.md (current milestone/commit)
- PROJECT_BRAIN/prompts/tester.md
- Implementation code being tested

### Refactor
Load universal context plus:
- PROJECT_BRAIN/02_ROADMAP.md (if relevant)
- PROJECT_BRAIN/prompts/refactor.md
- Code being refactored
- Existing tests

## Milestone-Specific Context

### Milestone 1 (Dummy Backend)
**Load**:
- orchestrator/
- speech/
- translate/
- tts/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/ (read only if necessary)
- database/ (read only if necessary)
- deploy/

### Milestone 2 (Whisper)
**Load**:
- speech/
- audio/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- translate/
- tts/
- api/
- deploy/

### Milestone 3 (Translation)
**Load**:
- translate/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- tts/
- api/
- deploy/

### Milestone 4 (TTS)
**Load**:
- tts/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- translate/
- api/
- deploy/

### Milestone 5 (Demucs)
**Load**:
- audio/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- translate/
- tts/
- api/
- deploy/

### Milestone 6 (Job Queue)
**Load**:
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/ (read only for integration)
- speech/
- translate/
- tts/
- api/

### Milestone 7 (Monitoring)
**Load**:
- monitoring/
- jobs/
- orchestrator/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- translate/
- tts/
- api/

### Milestone 8 (Webhook)
**Load**:
- webhook/
- jobs/
- database/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- translate/
- tts/
- api/

### Milestone 9 (Admin API)
**Load**:
- api/
- jobs/
- database/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- bot/
- speech/
- translate/
- tts/
- webhook/

### Milestone 10 (Production)
**Load**:
- Dockerfile
- docker-compose.prod.yml
- nginx.conf
- scripts/
- tests/
- PROJECT_BRAIN/

**Do NOT Load**:
- Core application code (only optimizations)

## Context Loading Order
Load files in this order for optimal context:
1. Universal PROJECT_BRAIN files (always first)
2. Role-specific prompt
3. Milestone-specific context
4. Code files (as needed)

## Token Optimization Tips
- Use grep_search instead of reading entire files when looking for specific patterns
- Use find_by_name to locate files before reading
- Read only the specific files needed for current task
- Use context window rules to avoid unnecessary file reads

## Example Context Loading Command

For Milestone 2, Implementer role:
```
Load:
1. PROJECT_BRAIN/01_ARCHITECTURE.md
2. PROJECT_BRAIN/04_CONSTITUTION.md
3. PROJECT_BRAIN/03_DECISIONS.md
4. PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
5. PROJECT_BRAIN/06_MODULE_MAP.md
6. PROJECT_BRAIN/07_PUBLIC_API.md
7. PROJECT_BRAIN/08_TESTING_GUIDE.md
8. PROJECT_BRAIN/09_AI_RULES.md
9. PROJECT_BRAIN/02_ROADMAP.md (Milestone 2 section)
10. PROJECT_BRAIN/prompts/implementer.md
11. src/universal_video_ai/speech/
12. src/universal_video_ai/audio/
13. tests/
```

This context loader ensures AI has necessary information while minimizing token usage.
