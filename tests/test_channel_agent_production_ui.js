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
