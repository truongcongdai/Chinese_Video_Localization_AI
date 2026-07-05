# Universal Video AI - AI Context Map

## Purpose
Defines which files AI should READ based on current commit/number. This dramatically reduces token usage by preventing AI from reading irrelevant files.

## How to Use
When AI starts work, check the current commit number and load ONLY the files specified for that commit.

## Commit Context Maps

### Commit 1-5: Foundation (Core Infrastructure)
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 6-10: Download Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/downloader.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/downloader/
- tests/test_downloader/

**DO NOT READ**:
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 11-15: Audio Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/audio.md
- PROJECT_BRAIN/modules/downloader.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- tests/test_audio/

**DO NOT READ**:
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 16-20: Speech Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/speech.md
- PROJECT_BRAIN/modules/audio.md
- PROJECT_BRAIN/modules/downloader.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- tests/test_speech/

**DO NOT READ**:
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 21-25: Translation Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/translate.md
- PROJECT_BRAIN/modules/speech.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- tests/test_translate/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 26-30: TTS Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/tts.md
- PROJECT_BRAIN/modules/translate.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- tests/test_tts/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 31-35: Mixer Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/mixer.md
- PROJECT_BRAIN/modules/tts.md
- PROJECT_BRAIN/modules/audio.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/audio/
- src/universal_video_ai/tts/
- src/universal_video_ai/mixer/
- tests/test_mixer/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 36-40: Timeline Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/timeline.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/timeline/
- tests/test_timeline/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 41-45: Render Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/render.md
- PROJECT_BRAIN/modules/mixer.md
- PROJECT_BRAIN/modules/timeline.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/render/
- tests/test_render/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 46-50: Orchestrator Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/orchestrator.md
- PROJECT_BRAIN/modules/render.md
- PROJECT_BRAIN/modules/mixer.md
- PROJECT_BRAIN/modules/tts.md
- PROJECT_BRAIN/modules/translate.md
- PROJECT_BRAIN/modules/speech.md
- PROJECT_BRAIN/modules/audio.md
- PROJECT_BRAIN/modules/downloader.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/render/
- src/universal_video_ai/orchestrator/
- tests/test_orchestrator/

**DO NOT READ**:
- src/universal_video_ai/bot/
- src/universal_video_ai/jobs/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 51-55: Jobs Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/jobs.md
- PROJECT_BRAIN/modules/orchestrator.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/orchestrator/
- src/universal_video_ai/jobs/
- tests/test_jobs/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 56-60: Bot Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/bot.md
- PROJECT_BRAIN/modules/orchestrator.md
- PROJECT_BRAIN/modules/jobs.md
- PROJECT_BRAIN/modules/downloader.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/orchestrator/
- src/universal_video_ai/jobs/
- src/universal_video_ai/downloader/
- src/universal_video_ai/bot/
- tests/test_bot/

**DO NOT READ**:
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- src/universal_video_ai/api/
- deploy/

### Commit 61-65: Webhook Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/webhook.md
- PROJECT_BRAIN/modules/jobs.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/jobs/
- src/universal_video_ai/webhook/
- tests/test_webhook/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/api/
- deploy/

### Commit 66-70: API Module
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- PROJECT_BRAIN/modules/api.md
- PROJECT_BRAIN/modules/jobs.md
- PROJECT_BRAIN/modules/orchestrator.md
- src/universal_video_ai/config/
- src/universal_video_ai/exceptions/
- src/universal_video_ai/logger/
- src/universal_video_ai/models/
- src/universal_video_ai/database/
- src/universal_video_ai/jobs/
- src/universal_video_ai/orchestrator/
- src/universal_video_ai/api/
- tests/test_api/

**DO NOT READ**:
- src/universal_video_ai/downloader/
- src/universal_video_ai/audio/
- src/universal_video_ai/speech/
- src/universal_video_ai/translate/
- src/universal_video_ai/tts/
- src/universal_video_ai/bot/
- src/universal_video_ai/render/
- src/universal_video_ai/mixer/
- src/universal_video_ai/timeline/
- src/universal_video_ai/webhook/
- deploy/

### Commit 71+: Production & Optimization
**READ**:
- PROJECT_BRAIN/01_ARCHITECTURE.md
- PROJECT_BRAIN/04_CONSTITUTION.md
- PROJECT_BRAIN/03_DECISIONS.md
- PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
- PROJECT_BRAIN/06_MODULE_MAP.md
- PROJECT_BRAIN/07_PUBLIC_API.md
- PROJECT_BRAIN/08_TESTING_GUIDE.md
- PROJECT_BRAIN/09_AI_RULES.md
- PROJECT_BRAIN/IMPORT_RULES.md
- PROJECT_BRAIN/GOLDEN_RULES.md
- Dockerfile
- docker-compose.prod.yml
- nginx.conf
- scripts/
- tests/

**DO NOT READ**:
- Core application code (only read if optimizing specific module)

## Universal Context (Always Load)
These PROJECT_BRAIN files should be loaded for ANY commit:
1. PROJECT_BRAIN/01_ARCHITECTURE.md
2. PROJECT_BRAIN/04_CONSTITUTION.md
3. PROJECT_BRAIN/03_DECISIONS.md
4. PROJECT_BRAIN/05_DEPENDENCY_GRAPH.md
5. PROJECT_BRAIN/06_MODULE_MAP.md
6. PROJECT_BRAIN/07_PUBLIC_API.md
7. PROJECT_BRAIN/08_TESTING_GUIDE.md
8. PROJECT_BRAIN/09_AI_RULES.md
9. PROJECT_BRAIN/IMPORT_RULES.md
10. PROJECT_BRAIN/GOLDEN_RULES.md
11. PROJECT_BRAIN/AI_MEMORY.md

## Token Savings Example
Without context map:
- AI reads entire codebase: ~50,000 tokens
- AI reads entire PROJECT_BRAIN: ~10,000 tokens
- Total: ~60,000 tokens

With context map (Commit 16-20 - Speech Module):
- AI reads relevant modules: ~8,000 tokens
- AI reads relevant PROJECT_BRAIN: ~10,000 tokens
- Total: ~18,000 tokens

**Savings: 70% reduction in token usage**

## AI Behavior
When AI starts work:
1. Check current commit number
2. Load universal PROJECT_BRAIN context
3. Load commit-specific files from this map
4. DO NOT read files in "DO NOT READ" section
5. If AI needs file outside map, explicitly request permission

This context map ensures AI focuses on relevant code while maintaining architectural awareness.
