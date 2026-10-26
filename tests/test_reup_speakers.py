import asyncio
from dataclasses import replace

import pytest

from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.speech.diarization import SpeakerTurn, assign_speakers, PyannoteDiarizer
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig
from universal_video_ai.web.store import Store


def test_speaker_assignment_is_stable_and_marks_mixed_speech():
    segments = [TranscriptSegment(0, 1, "A"), TranscriptSegment(1, 2, "B"),
                TranscriptSegment(2, 4, "both"), TranscriptSegment(5, 6, "unknown")]
    turns = [SpeakerTurn(0, 1, "X"), SpeakerTurn(1, 2, "Y"), SpeakerTurn(2, 4, "X"), SpeakerTurn(3, 4, "Y")]
    result = assign_speakers(segments, turns)
    assert [s.speaker_id for s in result] == ["speaker_0", "speaker_1", "speaker_0", None]
    assert [s.speaker_review for s in result] == [False, False, True, True]


def test_one_person_is_not_forced_into_two_voices():
    result = assign_speakers([TranscriptSegment(0, 2, "one"), TranscriptSegment(3, 5, "two")],
                             [SpeakerTurn(0, 5, "only")])
    assert {s.speaker_id for s in result} == {"speaker_0"}


@pytest.mark.parametrize("batched", [True, False])
def test_translation_and_retiming_keep_identity(batched):
    class Backend:
        async def translate(self, text, source, target):
            return "translated"
    backend = Backend()
    if batched:
        async def batch(texts, source, target):
            return ["translated"] * len(texts)
        backend.translate_batch = batch
    original = TranscriptSegment(0, 2, "source", "speaker_1", True)
    translated = asyncio.run(TranslateService(backend=backend).translate_segments([original], "zh", "vi"))
    assert translated[0] == replace(original, text="translated")
    retimed = LocalizationService._retime_segments(translated, [TranscriptSegment(1, 3, "clock")])
    assert retimed[0].speaker_id == "speaker_1" and retimed[0].speaker_review


def test_legacy_serialization_and_voice_fallback_are_unchanged():
    segment = TranscriptSegment(0, 1, "legacy")
    assert segment.to_dict() == {"start": 0, "end": 1, "text": "legacy"}
    assert TranscriptSegment.from_dict(segment.to_dict()) == segment
    service = LocalizationService(config=LocalizationConfig())
    assert service._voice_for_segment(replace(segment, speaker_id="speaker_1"), "old") == "old"


def test_voice_mapping_is_per_speaker_not_sentence_number():
    service = LocalizationService(config=LocalizationConfig(speaker_mode="manual", speaker_voices={"speaker_0":"male", "speaker_1":"female"}))
    assert service._voice_for_segment(TranscriptSegment(0, 1, "A", "speaker_1"), "fallback") == "female"
    with pytest.raises(RuntimeError, match="No voice assigned"):
        service._voice_for_segment(TranscriptSegment(0, 1, "A", "speaker_2"), "fallback")


def test_additive_store_migration_and_retry_keep_speaker_config(tmp_path):
    path = tmp_path / "db.sqlite"
    store = Store(path)
    owner = store.create_user("owner", "hash")
    config = {"mode": "manual", "voices": {"speaker_0":"voice"}}
    job = store.create_job(owner, "https://example.test", "vi", speaker_config=config)
    Store(path)
    assert Store(path).get_job(job.id).speaker_config == config
    store.update_job(job.id, status="error")
    assert store.retry_job(job.id, owner).speaker_config == config


def test_automatic_diarization_requires_explicit_local_model(tmp_path):
    with pytest.raises(RuntimeError, match="DIARIZATION_MODEL_PATH"):
        PyannoteDiarizer(str(tmp_path / "missing")).diarize(tmp_path / "audio.wav")
