# v1.0.0-rc1 Release Candidate Checklist

Engineering validation: 2026-09-09, 1000 passed / 5 skipped / 0 failed;
LIVE_PROVIDER_CALLS=0. Unchecked deployment/provider items are user acceptance
steps. Git equality, clean-tree, and tag checks run after the closure commit.

- [x] Social/provider credentials encrypted at rest; missing-key writes fail
- [x] Explicit idempotent token migration tested only on copies
- [x] Atomic owner-scoped autonomous download limit and restart recovery
- [x] Zero unintended tracked artifacts; local data hashes preserved
- [x] Key backup/migration and historical session rotation documented
- [x] OpenAPI has zero method/path collisions

- [ ] Dependencies installed inside the project virtual environment
- [ ] `.env` reviewed; no placeholders used as production secrets
- [ ] Database and owned/licensed assets backed up together
- [ ] Google/YouTube OAuth redirect URI and scopes verified
- [ ] YouTube owner connection verified
- [ ] Meta app, Page selection, permissions, and private/test destination verified
- [ ] Ollama enabled only when installed, running, and the model exists
- [ ] TTS provider and actual Vietnamese voice availability verified
- [ ] FFmpeg and ffprobe available on `PATH`
- [ ] yt-dlp import and a controlled owned-source probe verified
- [ ] Redis running where multi-process/shared caching is required
- [x] Targeted test matrix passes
- [x] Full `pytest -q` passes with `LIVE_PROVIDER_CALLS=0`
- [x] `python -m compileall -q src tests` passes
- [x] Relevant JavaScript passes `node --check`
- [x] End-to-end MOCK/CACHE/DRY_RUN acceptance reviewed
- [ ] LIVE provider mode remains disabled outside controlled acceptance
- [ ] Publishing privacy is private/dry-run for first acceptance
- [ ] Rights gate is cleared before any public publish
- [ ] Scheduler timezone, cadence, and missed-run policy reviewed
- [x] Cycle/download/render/publish concurrency limits reviewed
- [x] Backup restore procedure rehearsed on a copy
- [x] Windows startup accepted on the target Windows host
- [ ] Ubuntu startup accepted on the actual Ubuntu host
- [ ] Working tree clean; `HEAD == origin/dai2`
- [ ] Annotated `v1.0.0-rc1` points to the intended clean commit
