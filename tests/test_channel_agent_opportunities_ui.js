"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const UI = require("../src/universal_video_ai/web/static/content_opportunity_ui.js");
const appSource = fs.readFileSync(
  path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8",
);

test("default board is bounded to active states and top 20", () => {
  const params = new URLSearchParams(UI.listQuery({
    status: "draft,watch,approved", confidence: "", competition: "",
    sourceType: "", minScore: 0,
  }));
  assert.equal(params.get("status"), "draft,watch,approved");
  assert.equal(params.get("limit"), "20");
});

test("filters are normalized without accepting frontend metrics", () => {
  const params = new URLSearchParams(UI.listQuery({
    status: "watch", confidence: "low", competition: "unknown",
    sourceType: "gap", minScore: 999,
  }));
  assert.equal(params.get("confidence"), "low");
  assert.equal(params.get("competition"), "unknown");
  assert.equal(params.get("source_type"), "gap");
  assert.equal(params.get("min_score"), "100");
  assert.equal(params.has("evidence_score"), false);
});

test("score explanation preserves unavailable components", () => {
  const rows = UI.scoreRows({components: {
    trend: {available: true, signal: .8, normalized_points: 20},
    competitor_evidence: {available: false, signal: null, normalized_points: null},
  }});
  assert.equal(rows.length, 6);
  assert.equal(rows.find(row => row.key === "trend").signal, .8);
  assert.equal(rows.find(row => row.key === "competitor_evidence").available, false);
});

test("lifecycle actions match the explicit CP5 state machine", () => {
  assert.deepEqual(UI.transitions.draft, ["watch", "approved", "rejected"]);
  assert.deepEqual(UI.transitions.watch, ["draft", "approved", "rejected"]);
  assert.deepEqual(UI.transitions.approved, ["archived"]);
  assert.deepEqual(UI.transitions.rejected, ["draft"]);
  assert.deepEqual(UI.transitions.archived, []);
});

test("board uses stored API values and explicit manual actions", () => {
  assert.match(appSource, /item\.score_breakdown/);
  assert.match(appSource, /\/opportunities\/\$\{id\}\/refresh/);
  assert.match(appSource, /source_type: sourceType, source_id: String\(sourceId\)/);
  assert.match(appSource, /No Ollama request was made/);
  assert.doesNotMatch(appSource, /opportunities.*evidence_score:/);
});

test("approval copy explicitly excludes production side effects", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../src/universal_video_ai/web/static/index.html"), "utf8",
  );
  assert.match(html, /never creates, downloads, renders, or publishes content/);
  assert.match(appSource, /Approval is approval of the idea only/);
});
