# MVP Release Checklist

## Product flow

- [ ] Paste link video works for YouTube, Douyin, TikTok, Facebook, Instagram.
- [ ] Preset 9:16 works for TikTok / YouTube Shorts / Reels.
- [ ] Preset 16:9 works for standard YouTube videos.
- [ ] Default subtitle uses Karaoke unless the user changes it.
- [ ] Voice preview plays before creating a job.
- [ ] Preflight check catches missing credit, paid provider connection, invalid URL, and unsupported review/batch combination.
- [ ] Job progress clearly shows download, transcribe, translate, TTS, render, done/error.
- [ ] Download variants are available: MP4, SRT original, SRT translated, audio-only if generated.

## Quality gates

- [ ] Test at least 3 real sample videos: short vertical, horizontal YouTube, and 2–3 minute clip.
- [ ] Check transcript quality before render for noisy videos.
- [ ] Check subtitles are readable on mobile.
- [ ] Check rendered duration is close to source duration.
- [ ] Check final video passes prepublish/quality review.

## Operations

- [ ] `.env.example` contains every required config key and no real secrets.
- [ ] Redis is enabled in production or fallback behavior is explicitly accepted.
- [ ] Database backup is scheduled.
- [ ] `local_data` / output cleanup policy is configured.
- [ ] `/health` is monitored.
- [ ] GitHub Actions CI passes on the release branch.
- [ ] Docker smoke build passes before deployment.

## Beta

- [ ] 5–10 users test the complete flow.
- [ ] Record where users stop or misunderstand the UI.
- [ ] Collect voice/subtitle quality feedback.
- [ ] Fix only blockers before adding new features.
