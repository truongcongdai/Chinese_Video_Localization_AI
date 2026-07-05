# PROJECT_BRAIN - Knowledge Base

## Purpose
PROJECT_BRAIN is a comprehensive Knowledge Base that enables any AI (ChatGPT, Claude, Gemini, Copilot, DeepSeek, Qwen, etc.) to work consistently on the Universal Video AI project with the same architectural understanding and coding standards.

## Structure

```
PROJECT_BRAIN/
├── 01_ARCHITECTURE.md          # High-level architecture overview
├── 02_ROADMAP.md               # Development roadmap and milestones
├── 03_DECISIONS.md             # Architectural Decision Records (ADRs)
├── 04_CONSTITUTION.md          # Coding standards and rules
├── 05_DEPENDENCY_GRAPH.md      # Module dependency rules
├── 06_MODULE_MAP.md            # Module permissions and boundaries
├── 07_PUBLIC_API.md            # Public API documentation
├── 08_TESTING_GUIDE.md         # Testing guidelines
├── 09_AI_RULES.md              # AI-specific development rules
├── 10_CHANGELOG.md             # Project changelog
├── IMPORT_RULES.md             # Import dependency rules (NEW)
├── GOLDEN_RULES.md             # 10 immutable golden rules (NEW)
├── AI_CONTEXT_MAP.md          # Commit-based file access rules (NEW)
├── AI_MEMORY.md                # Known facts tracking (NEW)
├── modules/                    # Module-specific Knowledge Base (NEW)
│   ├── downloader.md
│   ├── audio.md
│   ├── speech.md
│   ├── translate.md
│   ├── tts.md
│   ├── mixer.md
│   ├── render.md
│   ├── timeline.md
│   ├── orchestrator.md
│   ├── bot.md
│   ├── jobs.md
│   ├── webhook.md
│   └── database.md
├── prompts/                     # Role-based AI prompts
│   ├── architect.md
│   ├── implementer.md
│   ├── reviewer.md
│   ├── tester.md
│   └── refactor.md
├── templates/                   # Document templates
│   ├── commit_template.md
│   ├── pr_template.md
│   ├── adr_template.md
│   ├── milestone_template.md
│   └── bugfix_template.md
└── ai/                          # AI-specific guidance
    ├── system_prompt.md
    ├── context_loader.md
    ├── review_checklist.md
    └── self_review.md
```

## New Knowledge Base Features

### 1. Module Documentation (modules/)
Each module now has a Knowledge Base entry with:
- **Responsibility**: What the module does
- **Must Never**: What the module should never do
- **Dependencies**: What the module depends on
- **Produces**: What the module outputs
- **Consumers**: Who uses this module
- **Thread Safe**: Whether the module is thread-safe
- **Singleton**: Whether the module is a singleton
- **Owner**: Which team owns this module
- **Stability Level**: 1-5 star rating of stability
- **AI Rules**: What AI needs to know before changing this module
- **Future**: Planned enhancements

**Example**:
```markdown
# DownloadService - Knowledge Base

## Responsibility
Downloads videos from various platforms

## Must Never
- Extract audio from video
- Call ffmpeg
- Call speech services

## Dependencies
- DownloaderFactory
- config/
- logger/

## Produces
- DownloadResult

## Consumers
- AudioPipeline
- LocalizationService

## Thread Safe
YES

## Singleton
NO

## Owner
Download Team

## Stability Level
★★★★★ Stable

## AI Rules
Changing this module requires:
- Architecture Review
- Protocol compliance check

## Future
- Support m3u8 streaming
```

### 2. Import Rules (IMPORT_RULES.md)
Defines allowed and forbidden import dependencies between modules with:
- Dependency hierarchy (5 layers)
- Allowed imports per layer
- Forbidden patterns with examples
- Module-specific import rules
- AI behavior when violations detected

**Example**:
```markdown
### Layer 1 (Adapters) MAY Import From:
- Layer 2 (Orchestrators)
- Layer 3 (Services)
- Layer 5 (Core)

**FORBIDDEN**:
- Layer 4 (Backends) - must use service layer
```

### 3. Golden Rules (GOLDEN_RULES.md)
10 immutable principles that guide all development:
1. Never Rewrite Working Code
2. Prefer Extension Over Modification
3. Composition Over Inheritance
4. One Commit One Concern
5. Every Feature Injectable
6. Every Backend Replaceable
7. Everything Testable
8. Protocol-Based Design
9. Immutable Configuration
10. Explicit Over Implicit

Each rule includes:
- Principle statement
- Examples of correct/incorrect usage
- Implementation examples
- AI behavior guidelines

### 4. AI Context Map (AI_CONTEXT_MAP.md)
Commit-based file access rules that reduce token usage by 70%:
- Defines which files to READ based on commit number
- Universal context (always load)
- Commit-specific context (load only relevant files)
- DO NOT READ sections (prevents reading irrelevant code)
- Token savings examples

**Example**:
```markdown
### Commit 16-20: Speech Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/modules/speech.md
- src/universal_video_ai/speech/
- tests/test_speech/

**DO NOT READ**:
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
```

### 5. AI Memory (AI_MEMORY.md)
Tracks known facts to prevent reading git history:
- Module completion status (COMPLETE/PARTIAL/TODO)
- Known facts about each module
- AI implications (what AI should know)
- Architecture decisions status
- Known issues
- Testing status
- Dependencies
- Performance characteristics
- Security considerations

**Example**:
```markdown
#### speech/
**Status**: PARTIAL
**Known Facts**:
- SpeechBackend protocol defined
- SpeechService implemented
- WhisperBackend implemented

**AI Implications**:
- SpeechBackend protocol is frozen (do not modify)
- Can add new backend implementations
- Do not modify TranscriptionResult structure
```

## How to Use PROJECT_BRAIN

### For AI Development
When AI starts work on this project:

1. **Load Universal Context** (always):
   - 01_ARCHITECTURE.md
   - 04_CONSTITUTION.md
   - 03_DECISIONS.md
   - 05_DEPENDENCY_GRAPH.md
   - 06_MODULE_MAP.md
   - 07_PUBLIC_API.md
   - 08_TESTING_GUIDE.md
   - 09_AI_RULES.md
   - IMPORT_RULES.md
   - GOLDEN_RULES.md
   - AI_MEMORY.md

2. **Load Role-Specific Prompt**:
   - prompts/architect.md (if designing)
   - prompts/implementer.md (if coding)
   - prompts/reviewer.md (if reviewing)
   - prompts/tester.md (if testing)
   - prompts/refactor.md (if refactoring)

3. **Load Commit-Specific Context** (from AI_CONTEXT_MAP.md):
   - Check current commit number
   - Load only relevant modules
   - DO NOT read irrelevant files

4. **Load Module Knowledge Base** (for relevant modules):
   - modules/[module_name].md
   - Check stability level
   - Check AI rules
   - Check must never rules

### For Human Development
When humans work on this project:

1. **Read Module Knowledge Base** before changing a module
2. **Follow Golden Rules** for all decisions
3. **Check Import Rules** before adding dependencies
4. **Update AI Memory** when modules are completed
5. **Use Templates** for commits, PRs, ADRs

## Benefits

### For AI
- **Consistency**: All AIs work with same knowledge
- **Efficiency**: 70% reduction in token usage via context maps
- **Quality**: Golden rules prevent common mistakes
- **Safety**: Import rules prevent architectural violations
- **Context**: Module KB provides quick reference

### For Humans
- **Onboarding**: Quick understanding of module responsibilities
- **Decision Making**: Golden rules guide architectural decisions
- **Code Review**: Clear standards to review against
- **Knowledge Sharing**: Centralized knowledge base
- **AI Collaboration**: Humans and AI share same context

### For Project
- **Stability**: Stability levels indicate safe modules to change
- **Maintainability**: Clear module boundaries and dependencies
- **Testability**: Golden rules ensure testable code
- **Scalability**: Protocol-based design allows easy extension
- **Documentation**: Comprehensive knowledge base

## Token Optimization

Without PROJECT_BRAIN:
- AI reads entire codebase: ~50,000 tokens
- AI reads git history: ~20,000 tokens
- Total: ~70,000 tokens

With PROJECT_BRAIN:
- AI reads universal context: ~12,000 tokens
- AI reads commit-specific context: ~6,000 tokens
- AI reads module KB: ~2,000 tokens
- Total: ~20,000 tokens

**Savings: 71% reduction in token usage**

## AI Independence

PROJECT_BRAIN makes the project independent of any specific AI:
- ChatGPT ✅
- Claude ✅
- Gemini ✅
- Copilot ✅
- DeepSeek ✅
- Qwen ✅

All AIs load the same PROJECT_BRAIN and work with the same architectural understanding.

## Maintenance

### When to Update PROJECT_BRAIN

1. **Module Completed**: Update AI_MEMORY.md module status
2. **New Module**: Create modules/[module_name].md
3. **Architecture Change**: Update relevant ADRs in 03_DECISIONS.md
4. **New Rule**: Add to GOLDEN_RULES.md or IMPORT_RULES.md
5. **New Milestone**: Update AI_CONTEXT_MAP.md
6. **API Change**: Update 07_PUBLIC_API.md

### Review Schedule
- Monthly: Review AI_MEMORY.md for accuracy
- Quarterly: Review Golden Rules for relevance
- Per Milestone: Update AI_CONTEXT_MAP.md

## Success Metrics

PROJECT_BRAIN is successful when:
- AI consistently follows architectural rules
- Token usage is reduced by >70%
- Code reviews are faster (clearer standards)
- New developers onboard faster
- Architectural violations are rare
- All AIs produce consistent code quality

## Conclusion

PROJECT_BRAIN transforms the project from documentation to a true Knowledge Base that enables consistent, high-quality development regardless of which AI or human is working on the project. This is the level of professionalization that large companies achieve with architecture, ADRs, coding standards, and API contracts.
