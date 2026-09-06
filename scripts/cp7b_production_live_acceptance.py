"""Exercise CP7B from approved CP7A records using a temporary database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from universal_video_ai.channel_agent.production import ProductionQueueService
from universal_video_ai.channel_agent.production_render import ProductionRenderService
from universal_video_ai.web.store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--voice", default="vi-VN-HoaiMyNeural")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    store = Store(output_dir / "cp7b-acceptance.sqlite3")
    token = uuid.uuid4().hex[:10]
    user_id = store.create_user(f"cp7b-{token}", "acceptance-only")
    brief = {
        "working_title": "CP7B owned-source acceptance",
        "target_format": "short_form",
        "target_duration_min": 1,
        "target_duration_max": 1,
        "voice_direction": {"language": "Vietnamese"},
    }
    item_id = store.insert_production_item_with_tasks(user_id, {
        "opportunity_id": int(token[:6], 16),
        "status": "planning",
        "priority": 100,
        "opportunity_rank_score": 1,
        "working_title": brief["working_title"],
        "selected_angle": "Opening localization coverage",
        "target_format": "short_form",
        "target_duration_min": 1,
        "target_duration_max": 1,
        "production_brief": brief,
        "rights_status": "owned",
        "rights_gate_status": "cleared",
        "planning_ready": True,
        "rights_ready": True,
    }, ProductionQueueService.build_tasks(brief, "short_form"))

    blueprint = store.insert_production_asset(
        user_id, item_id, asset_type="script_blueprint",
        payload={
            "schema_version": "cp7a-v1", "section_count": 2,
            "narration_wpm": 145,
            "sections": [{"section_index": 1}, {"section_index": 2}],
        },
    )
    sections = []
    for index, (text, duration) in enumerate((
        ("Xin chào, chào mừng bạn đến đây.", 2.2),
        ("Video này đã được bản địa hóa đầy đủ.", 2.5),
    ), start=1):
        asset = store.insert_production_asset(
            user_id, item_id, asset_type="script_section",
            asset_key=f"{index:02d}",
            payload={
                "section_index": index,
                "content": text,
                "target_duration_minutes": duration / 60,
                "blueprint_asset_id": blueprint["id"],
                "blueprint_version": blueprint["version"],
                "budget_acceptable": True,
            },
        )
        sections.append({
            "asset_id": asset["id"], "version": asset["version"],
            "section_index": index,
        })
    script = store.insert_production_asset(
        user_id, item_id, asset_type="script_draft", status="approved",
        payload={
            "blueprint_asset_id": blueprint["id"],
            "blueprint_version": blueprint["version"],
            "section_count": 2,
            "source_section_versions": sections,
            "budget_acceptable": True,
        },
    )
    source_ref = {
        "source_script_asset_id": script["id"],
        "source_script_version": script["version"],
    }
    for asset_type, payload in (
        ("visual_plan", {
            **source_ref,
            "scenes": [{
                "section": 1, "scene": 1, "purpose": "localization base",
                "visual_description": "Use the owner-attested source video",
                "source_strategy": "owned_asset", "duration": 8,
                "rights_notes": "Owned acceptance source",
            }],
        }),
        ("voice_plan", {
            **source_ref,
            "language": "Vietnamese",
            "voice_character": "clear narrator", "tone": "natural",
            "pace": "conversational", "energy": "medium",
            "pronunciation_guidance": [], "pause_emphasis_guidance": [],
            "voice_id": args.voice,
            "section_delivery_notes": [
                {"section": 1, "speaker_role": "narrator"},
                {"section": 2, "speaker_role": "narrator"},
            ],
        }),
        ("metadata_package", {
            **source_ref,
            "title_candidates": ["CP7B acceptance"],
            "recommended_title": "CP7B acceptance",
            "description": "Owned-source localization acceptance",
            "hashtags": ["localization"], "tags": ["Vietnamese"],
            "chapter_suggestions": ["00:00 Opening"],
            "cta": "Review the localized result",
            "search_intent": "Vietnamese localized video",
            "keyword_alignment": "Vietnamese localization",
            "audience_alignment": "Vietnamese viewers",
        }),
    ):
        store.insert_production_asset(
            user_id, item_id, asset_type=asset_type,
            status="approved", payload=payload,
        )

    service = ProductionRenderService(
        store, output_root=output_dir / "render-output"
    )
    job = service.submit(
        user_id, item_id,
        source_video_path=str(args.source.resolve()),
        source_media_rights="owned",
        voice_id=args.voice,
        speaker_mapping={"narrator": args.voice},
        source_subtitle_boxes=[
            {"start": 0.0, "end": 2.2, "x": 180, "y": 286, "width": 280, "height": 34},
            {"start": 2.5, "end": 5.0, "x": 180, "y": 286, "width": 280, "height": 34},
        ],
    )
    completed = service.run(user_id, item_id, job["id"])
    report = {
        "database": str(store.db_path),
        "production_item_id": item_id,
        "render_job_id": job["id"],
        "approved_asset_refs": completed["approved_asset_refs"],
        "status": completed["status"],
        "stage": completed["current_stage"],
        "output_path": completed["output_path"],
        "qc": completed["qc"],
        "voice_plan_asset_id": completed["approved_asset_refs"]["voice_plan"]["asset_id"],
        "voice_used": args.voice,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if completed["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
