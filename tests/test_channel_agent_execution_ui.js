"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const UI = require("../src/universal_video_ai/web/static/content_execution_ui.js");
const appSource = fs.readFileSync(
  path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8",
);
const html = fs.readFileSync(
  path.join(__dirname, "../src/universal_video_ai/web/static/index.html"), "utf8",
);

test("asset lifecycle is explicitly human gated", () => {
  assert.deepEqual(UI.statusActions.draft, ["review", "approved", "rejected"]);
  assert.deepEqual(UI.statusActions.review, ["draft", "approved", "rejected"]);
  assert.deepEqual(UI.statusActions.approved, ["superseded"]);
});

test("section resume identifies only the next incomplete plan", () => {
  const next = UI.nextMissingSection([
    {plan: {section_id: "sec_01"}, latest_asset: {id: 1}},
    {plan: {section_id: "sec_02"}, latest_asset: null},
    {plan: {section_id: "sec_03"}, latest_asset: null},
  ]);
  assert.equal(next, "sec_02");
});

test("generation routes remain item and section scoped", () => {
  assert.equal(UI.generationPath(7, "script_blueprint"), "/api/channel-agent/production/7/script/blueprint");
  assert.equal(UI.generationPath(7, "script_section", "sec 1"), "/api/channel-agent/production/7/script/sections/sec%201/generate");
  assert.equal(UI.generationPath(7, "visual_plan"), "/api/channel-agent/production/7/visual-plan/generate");
  assert.throws(() => UI.generationPath(7, "render_video"));
});

test("job progress is deterministic", () => {
  assert.equal(UI.jobProgress({progress_current: 4, progress_total: 8}), "4 / 8 (50%)");
});

test("UI exposes section editing version history resume and cancellation", () => {
  assert.match(appSource, /Save manual edit as new version/);
  assert.match(appSource, /Resume \/ Generate all remaining/);
  assert.match(appSource, /data-execution-versions/);
  assert.match(appSource, /production-generation\/jobs\/\$\{jobId\}/);
  assert.match(appSource, /Cancellation requested\. Completed section versions are preserved/);
});

test("asset and rights readiness are visibly separate", () => {
  assert.match(appSource, /Asset ready/);
  assert.match(appSource, /Rights ready/);
  assert.match(appSource, /source-media permission/);
});

test("CP7A UI has no render upload publish or TTS execution control", () => {
  assert.match(html, /content_execution_ui\.js/);
  const start = appSource.indexOf("let activeExecutionItemId");
  const end = appSource.indexOf("$(\"#production-refresh-list\")", start);
  const cp7a = appSource.slice(start, end);
  assert.doesNotMatch(cp7a, /render-video|upload-video|publish-video|execute-tts/);
});
