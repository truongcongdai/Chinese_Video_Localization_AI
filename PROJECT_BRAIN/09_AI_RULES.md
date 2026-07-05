# Universal Video AI - AI Development Rules

## Purpose
This document defines rules for AI assistants (ChatGPT, Copilot, Claude, Qwen, Gemini, etc.) working on Universal Video AI. These rules ensure consistency with architecture and prevent AI from introducing architectural drift.

## Context Loading

### Mandatory Context for All AI Sessions
Before starting work, AI MUST load these files in order:

1. **PROJECT_BRAIN/01_ARCHITECTURE.md** - Understand architecture
2. **PROJECT_BRAIN/04_CONSTITUTION.md** - Learn coding rules
3. **PROJECT_BRAIN/03_DECISIONS.md** - Review architectural decisions
4. **PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md** - Check dependency rules
5. **PROJECT_BRAIN/06_MODULE_MAP.md** - Verify module permissions
6. **PROJECT_BRAIN/08_TESTING_GUIDE.md** - Follow testing standards
7. **PROJECT_BRAIN/02_ROADMAP.md** - Understand current milestone

### Context Window Optimization
AI should ONLY read relevant modules for current milestone:

**Example - Milestone 2 (Whisper)**:
- ✅ READ: speech/, audio/, tests/, PROJECT_BRAIN/
- ❌ DO NOT READ: bot/, translate/, tts/, api/, deploy/

**Example - Milestone 6 (Job Queue)**:
- ✅ READ: jobs/, orchestrator/, tests/, PROJECT_BRAIN/
- ❌ DO NOT READ: bot/, speech/, translate/, tts/, api/

## Milestone-Based Development

### Always Specify Current Milestone
Every AI request MUST include:
- Current milestone number
- Specific commit/step within milestone
- Acceptance criteria being addressed

**Example Prompt**:
```
I am working on Milestone 2, Commit 25: Implement WhisperBackend.
Please help me implement the transcribe method according to ADR-002 and the SpeechBackend protocol.
```

### Follow Milestone Commit Structure
Each milestone is divided into specific commits. AI MUST:
- Work on one commit at a time
- Complete commit before moving to next
- Follow commit order defined in roadmap

**Example - Milestone 1**:
1. Commit 24: LocalizationService factory
2. Commit 25: Dummy Whisper backend
3. Commit 26: Dummy Translate backend
4. Commit 27: Dummy TTS backend
5. Commit 28: Pipeline integration
6. Commit 29: Telegram integration
7. Commit 30: Integration tests

## Code Generation Rules

### Rule 1: Never Modify Frozen Interfaces
AI MUST NOT:
- Change protocol definitions
- Change service class signatures
- Change dataclass fields
- Remove public methods
- Rename classes or methods

**AI Response to Interface Change Request**:
```
I cannot modify the SpeechBackend protocol as it is frozen per PROJECT_BRAIN/06_MODULE_MAP.md.
Protocol changes require an Architectural Decision Record (ADR) and Tech Lead approval.
Please refer to PROJECT_BRAIN/03_DECISIONS.md for the decision process.
```

### Rule 2: Follow Coding Constitution
AI MUST:
- Use pathlib (never os.path)
- Use logging (never print())
- Use type hints (100% coverage)
- Use dataclasses for data
- Use protocols for interfaces
- Use dependency injection
- Follow naming conventions

**AI Violation Detection**:
If AI generates code with:
- `print()` → Reject and use logging
- `os.path` → Reject and use pathlib
- Missing type hints → Reject and add them
- Global variables → Reject and use DI

### Rule 3: Respect Module Permissions
AI MUST check PROJECT_BRAIN/06_MODULE_MAP.md before modifying files:

**Frozen Module Changes**:
```
User: "Add a new method to DownloadService"
AI: "DownloadService is frozen per PROJECT_BRAIN/06_MODULE_MAP.md.
I can add a private method (_method) but cannot change the public signature.
Is a private method acceptable, or should we create an ADR for this change?"
```

**Mutable Module Changes**:
```
User: "Add a new command to TelegramBot"
AI: "TelegramBot is mutable. I will add the new command following the existing pattern.
I'll also add unit tests per PROJECT_BRAIN/08_TESTING_GUIDE.md."
```

### Rule 4: Generate Tests First
AI MUST:
- Write unit tests before implementation
- Write integration tests for new features
- Follow test naming conventions
- Use fixtures for common setup
- Mock external dependencies

**Example Workflow**:
```
User: "Implement WhisperBackend.transcribe method"
AI: "I will first write the unit test, then implement the method.

[Test code]
def test_whisper_backend_transcribe():
    mock_whisper = Mock()
    mock_whisper.transcribe.return_value = {"text": "test"}
    backend = WhisperBackend(whisper=mock_whisper)
    result = backend.transcribe(Path("/tmp/audio.wav"))
    assert result == "test"

[Implementation code]
class WhisperBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        # Implementation
        pass
```

### Rule 5: Follow Dependency Graph
AI MUST ensure dependencies follow PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md:

**Violation Detection**:
```
User: "Add WhisperBackend import to TelegramBot"
AI: "This violates the dependency graph. TelegramBot (Layer 1) should not directly
depend on WhisperBackend (Layer 4). Instead, TelegramBot should use SpeechService (Layer 3),
which internally uses SpeechBackend protocol. I will refactor accordingly."
```

## Error Handling

### AI Must Handle Errors Correctly
When AI encounters an error:

1. **Check Constitution**: Is the code following all rules?
2. **Check Dependencies**: Are dependencies correct per graph?
3. **Check Permissions**: Is the module frozen?
4. **Check ADRs**: Is there a relevant decision record?
5. **Propose Solution**: Suggest the correct approach

**Example Error Response**:
```
I cannot implement this as requested because:
1. The module is frozen per PROJECT_BRAIN/06_MODULE_MAP.md
2. This would violate the dependency graph in PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md

Alternative approach:
- Create a new implementation of the existing protocol
- Use dependency injection to swap implementations
- Follow the existing pattern in similar modules

Would you like me to proceed with the alternative?
```

## Code Review Simulation

### AI Must Self-Review Before Outputting
Before providing code, AI MUST check:

- [ ] No print() statements
- [ ] No os.path usage
- [ ] No global variables
- [ ] All functions have type hints
- [ ] All public functions have docstrings
- [ ] Protocol compliance verified
- [ ] Dependency graph followed
- [ ] Module permissions respected
- [ ] Tests included
- [ ] Constitution followed

**AI Self-Correction**:
```
I noticed my implementation used print() for debugging. Let me correct that
to use logging per PROJECT_BRAIN/04_CONSTITUTION.md.
```

## Token Management

### AI Should Optimize Context Window
AI MUST:
- Only read relevant files for current milestone
- Use PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md context window rules
- Avoid reading entire codebase
- Focus on specific modules being modified

**Example**:
```
User: "Help with Milestone 2"
AI: "I will load:
- PROJECT_BRAIN/ (all files)
- src/universal_video_ai/speech/
- src/universal_video_ai/audio/
- tests/

I will NOT load:
- bot/
- translate/
- tts/
- api/
- deploy/
```

## Definition of Done Compliance

### AI Must Ensure Code Meets DoD
Before considering code complete, AI MUST verify:

- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] mypy passes (no type errors)
- [ ] ruff passes (no linting errors)
- [ ] Black formatted (code style)
- [ ] Logging added (no print())
- [ ] No TODO comments
- [ ] Type hints 100%
- [ ] Backward compatible
- [ ] Public API unchanged
- [ ] Documentation updated

**AI DoD Check**:
```
I have implemented the feature. Let me verify DoD compliance:

✓ Unit tests written and pass
✓ Type hints added (100%)
✓ Logging used (no print())
✓ Black formatted
✓ Protocol compliant
✓ Module permissions respected
✓ Documentation updated

The code is ready for review.
```

## Common AI Mistakes to Avoid

### ❌ Mistake 1: Bypassing Service Layer
```
User: "Add Whisper call to bot handler"
AI (BAD): "I'll add import whisper and call it directly"

AI (GOOD): "Per PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md, bot should not directly
call Whisper. Instead, use SpeechService which implements the protocol.
I will inject SpeechService into the bot."
```

### ❌ Mistake 2: Changing Frozen Interfaces
```
User: "Add a parameter to SpeechBackend.transcribe"
AI (BAD): "I'll update the protocol"

AI (GOOD): "SpeechBackend protocol is frozen per PROJECT_BRAIN/06_MODULE_MAP.md.
Protocol changes require an ADR and Tech Lead approval.
Alternative: Add a new method to the protocol or use configuration."
```

### ❌ Mistake 3: Using os.path
```
User: "Get file extension"
AI (BAD): "import os; ext = os.path.splitext(file)[1]"

AI (GOOD): "from pathlib import Path; ext = Path(file).suffix"
```

### ❌ Mistake 4: Missing Type Hints
```
User: "Add a function to process data"
AI (BAD): "def process(data): return data * 2"

AI (GOOD): "def process(data: str) -> str: return data * 2"
```

### ❌ Mistake 5: No Tests
```
User: "Implement this feature"
AI (BAD): [Provides implementation only]

AI (GOOD): "I'll first write the test, then implement:
[Test code]
[Implementation code]"
```

## AI Communication Style

### AI Should Be Direct and Fact-Based
- Do not use acknowledgment phrases ("Great idea!", "I agree")
- Jump directly into addressing the request
- Reference specific PROJECT_BRAIN documents
- Provide file paths and line numbers
- Be concise and avoid verbose explanations

**Example AI Response**:
```
Per PROJECT_BRAIN/06_MODULE_MAP.md, DownloadService is frozen. I cannot change
the public signature. However, I can add a private helper method.

Alternative: Create a new service that wraps DownloadService with additional
functionality. This follows the composition principle in ADR-009.

Would you like me to proceed with the wrapper approach?
```

## AI Learning and Adaptation

### AI Should Learn from Project Context
AI MUST:
- Remember PROJECT_BRAIN documents across sessions
- Apply lessons from previous milestones
- Follow established patterns
- Propose improvements consistent with architecture

**Example Learning**:
```
Based on Milestone 2 (Whisper), I see the pattern:
1. Protocol defines interface
2. Backend implements protocol
3. Service uses backend via protocol
4. Factory creates service with backend

For Milestone 3 (Translation), I will follow the same pattern:
1. Use existing TranslateBackend protocol
2. Implement GoogleTranslator
3. TranslateService already uses protocol
4. Update factory to inject GoogleTranslator
```

## AI Escalation

### When AI Cannot Proceed
If AI encounters a situation requiring:
- Protocol/interface changes
- Architecture modifications
- Frozen module changes
- Dependency graph violations

AI MUST:
1. Reference specific PROJECT_BRAIN document
2. Explain the constraint
3. Propose alternative approach
4. Request human decision if needed

**Escalation Example**:
```
I cannot implement this as requested because it violates PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md.
The proposed change would create an upward dependency from Layer 3 to Layer 1.

Per PROJECT_BRAIN/03_DECISIONS.md, this requires an ADR and Tech Lead approval.

Options:
1. Create ADR-013 to document this architectural change
2. Refactor to follow existing dependency graph
3. Use callback pattern to avoid direct dependency

Which approach would you like to pursue?
```

## AI Quality Assurance

### AI Must Verify Code Quality
Before outputting code, AI MUST run mental checks:

**Type Safety**:
- Are all types correctly hinted?
- Will mypy pass?
- Are Optional types handled correctly?

**Error Handling**:
- Are exceptions caught and handled?
- Are specific exceptions used (not bare except)?
- Are error messages descriptive?

**Resource Management**:
- Are files opened with context managers?
- Are resources cleaned up?
- Are temporary files handled?

**Concurrency**:
- Is shared state protected?
- Are race conditions avoided?
- Are locks used correctly?

**Security**:
- Is user input validated?
- Are secrets not logged?
- Is SQL injection prevented?

## AI Success Metrics

AI is successful when:
- Code follows PROJECT_BRAIN documents
- Code passes all DoD checks
- Code includes comprehensive tests
- Code respects module permissions
- Code follows dependency graph
- Code is consistent with architecture

## Enforcement

These AI rules are enforced through:
1. Context loading requirements
2. Milestone-based development
3. Code generation rules
4. Error handling procedures
5. Self-review requirements
6. Quality assurance checks

AI assistants that consistently violate these rules should be:
- Reconfigured with stricter context loading
- Given explicit PROJECT_BRAIN references
- Requested to follow specific patterns
- Escalated to human review for architectural decisions

This document ensures AI assistants contribute consistently to Universal Video AI's architecture and quality standards.
