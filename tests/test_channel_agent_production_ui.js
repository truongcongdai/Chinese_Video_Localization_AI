"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const UI = require("../src/universal_video_ai/web/static/content_production_ui.js");
const appSource = fs.readFileSync(
  path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8",
);
const html = fs.readFileSync(
  path.join(__dirname, "../src/universal_video_ai/web/static/index.html"), "utf8",
);

test("active queue is bounded to 50 and server-observed filters", () => {
  const params = new URLSearchParams(UI.listQuery({
    status: "", minPriority: 500, rights: "research_only", targetFormat: "long_form",
  }));
  assert.equal(params.get("status"), UI.activeStatuses);
  assert.equal(params.get("min_priority"), "100");
  assert.equal(params.get("rights"), "research_only");
  assert.equal(params.get("target_format"), "long_form");
  assert.equal(params.get("limit"), "50");
  assert.equal(params.has("user_id"), false);
  assert.equal(params.has("opportunity_rank_score"), false);
});

test("item lifecycle matches explicit CP6 transitions", () => {
  assert.deepEqual(UI.itemTransitions.queued, ["planning", "cancelled"]);
  assert.deepEqual(UI.itemTransitions.planning, ["ready", "blocked", "cancelled"]);
  assert.deepEqual(UI.itemTransitions.in_progress, ["completed", "blocked", "cancelled"]);
  assert.deepEqual(UI.itemTransitions.completed, []);
  assert.deepEqual(UI.itemTransitions.cancelled, []);
});

test("task controls respect dependencies and optional skip", () => {
  assert.deepEqual(UI.taskActions({status: "pending", required: true}), ["blocked"]);
  assert.deepEqual(UI.taskActions({status: "ready", required: true}), ["in_progress", "blocked"]);
  assert.deepEqual(UI.taskActions({status: "ready", required: false}), ["in_progress", "blocked", "skipped"]);
  assert.deepEqual(UI.taskActions({status: "in_progress", required: true}), ["completed", "blocked"]);
  assert.deepEqual(UI.taskActions({status: "completed", required: true}), []);
});

test("progress is deterministic required-task progress", () => {
  assert.equal(UI.progressLabel({completed_required: 3, total_required: 6, percent: 50}), "3 / 6 (50%)");
});

test("approved opportunity integration and sync use explicit CP6 APIs", () => {
  assert.match(appSource, /item\.status === "approved"/);
  assert.match(appSource, /Create Production Item/);
  assert.match(appSource, /\/api\/channel-agent\/production/);
  assert.match(appSource, /\/production\/\$\{id\}\/sync/);
  assert.match(appSource, /task states, blockers, and production notes were preserved/);
});

test("UI distinguishes planning from rights and exposes no publish action", () => {
  assert.match(html, /Production Queue/);
  assert.match(appSource, /Planning ready/);
  assert.match(appSource, /Rights ready/);
  assert.match(appSource, /Idea approval is not source-media permission/);
  assert.doesNotMatch(html.slice(html.indexOf('id="production-queue"'), html.indexOf("<!-- Content OS Panel -->")), /Publish|Upload|Render/);
});

test("CP7A section rows retain latest versions and budget metrics", () => {
  const blueprint = {id: 20, payload: {sections: [
    {section_index: 1, title: "Opening", target_words: 1000, target_duration_minutes: 7},
    {section_index: 2, title: "Conflict", target_words: 1100, target_duration_minutes: 8},
  ]}};
  const rows = UI.sectionRows(blueprint, [
    {id: 10, asset_type: "script_section", asset_key: "01", version: 1, status: "draft",
      payload: {blueprint_asset_id: 20, actual_words: 850, estimated_duration_minutes: 5.9, completion_percentage: 85}},
    {id: 11, asset_type: "script_section", asset_key: "01", version: 2, status: "review",
      payload: {blueprint_asset_id: 20, actual_words: 1020, estimated_duration_minutes: 7, completion_percentage: 102}},
    {id: 12, asset_type: "script_section", asset_key: "02", version: 3, status: "draft",
      payload: {blueprint_asset_id: 19, actual_words: 9999, estimated_duration_minutes: 99,
        completion_percentage: 999}},
  ]);
  assert.equal(rows[0].assetId, 11);
  assert.equal(rows[0].version, 2);
  assert.equal(rows[0].actual_words, 1020);
  assert.equal(rows[1].status, "missing");
});

test("CP7A section rows distinguish partial from acceptable and approved", () => {
  const blueprint = {id: 20, payload: {sections: [
    {section_index: 1, title: "Opening", target_words: 1088, target_duration_minutes: 7},
    {section_index: 2, title: "Conflict", target_words: 1088, target_duration_minutes: 7},
    {section_index: 3, title: "End", target_words: 1088, target_duration_minutes: 7},
  ]}};
  const rows = UI.sectionRows(blueprint, [
    {id: 10, asset_type: "script_section", asset_key: "01", version: 2, status: "draft",
      payload: {blueprint_asset_id: 20, actual_words: 225, completion_percentage: 20.7}},
    {id: 11, asset_type: "script_section", asset_key: "02", version: 1, status: "draft",
      payload: {blueprint_asset_id: 20, actual_words: 908, completion_percentage: 83.5,
        budget_acceptable: true}},
    {id: 12, asset_type: "script_section", asset_key: "03", version: 1, status: "approved",
      payload: {blueprint_asset_id: 20, actual_words: 1088, completion_percentage: 100,
        budget_acceptable: true}},
  ]);
  assert.equal(rows[0].status, "partial");
  assert.equal(rows[0].action, "resume");
  assert.equal(UI.sectionStatusLabel(rows[0]), "PARTIAL");
  assert.equal(rows[1].status, "draft_acceptable");
  assert.equal(UI.sectionStatusLabel(rows[1]), "DRAFT/ACCEPTABLE");
  assert.equal(rows[1].action, "regenerate");
  assert.equal(UI.sectionStatusLabel(rows[2]), "APPROVED");
});

test("CP7A readiness keeps asset and rights state separate", () => {
  const label = UI.readinessLabel({
    planning_ready: true, asset_ready: true, qa_status: "approved",
    rights_ready: false, rights_gate: "research_only",
  });
  assert.match(label, /assets ready/);
  assert.match(label, /QA approved/);
  assert.match(label, /rights research_only/);
});

test("Production Queue integrates CP7A controls without client user_id", () => {
  assert.match(appSource, /CP7A Script &amp; Asset Production/);
  assert.match(appSource, /assets\/script\/blueprints/);
  assert.match(appSource, /assets\/script\/resume/);
  assert.match(appSource, /assets\/script\/drafts/);
  assert.match(appSource, /data-production-approve/);
  assert.match(appSource, /data-production-review/);
  assert.match(appSource, /production-run-asset-qa/);
  for (const panel of [
    "script_blueprint", "script_section", "script_draft", "visual_plan",
    "voice_plan", "thumbnail_brief", "metadata_package",
  ]) {
    assert.match(appSource, new RegExp('\\["' + panel + '",'));
  }
  assert.match(appSource, /class="production-asset-panel"/);
  assert.match(appSource, /QA \/ Asset Package/);
  const section = appSource.slice(
    appSource.indexOf("function productionAssetWorkspace"),
    appSource.indexOf("// ---------------- Content OS"),
  );
  assert.doesNotMatch(section, /user_id/);
  assert.doesNotMatch(section, /final TTS created|render final|publish video/i);
  assert.match(appSource, /Resume in progress/);
  assert.match(appSource, /syncProductionGenerationPolling/);
  assert.match(appSource, /productionGenerationPollInFlight/);
});
