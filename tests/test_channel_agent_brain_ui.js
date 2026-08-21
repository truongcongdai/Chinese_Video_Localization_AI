"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const ContentBrainUI = require("../src/universal_video_ai/web/static/content_brain_ui.js");

const modes = [
  "opportunity_analysis",
  "content_angles",
  "title_hooks",
  "longform_outline",
];

test("every selected mode is sent unchanged in the analyze payload", () => {
  for (const mode of modes) {
    const payload = ContentBrainUI.buildAnalyzePayload({
      mode,
      selectorType: "candidate",
      selectorId: 12,
      allowLowConfidence: true,
    });
    assert.equal(payload.request_type, mode);
    assert.equal(payload.selector_type, "candidate");
    assert.equal(payload.selector_id, "12");
    assert.equal(payload.allow_low_confidence, true);
  }
});

test("unknown modes fail instead of falling back to opportunity analysis", () => {
  assert.throws(
    () => ContentBrainUI.buildAnalyzePayload({ mode: "chat", selectorType: "candidate" }),
    /Unsupported Content Brain request type/,
  );
});

test("each mode selects exactly one matching result layout", () => {
  const expectedFlag = {
    opportunity_analysis: "showOpportunity",
    content_angles: "showAngles",
    title_hooks: "showTitlesHooks",
    longform_outline: "showOutline",
  };
  const flags = Object.values(expectedFlag);
  for (const mode of modes) {
    const view = ContentBrainUI.modeView({ request_type: mode });
    assert.equal(view.mode, mode);
    for (const flag of flags) assert.equal(view[flag], flag === expectedFlag[mode]);
  }
});

test("starting a new request clears the active run identity", () => {
  const state = ContentBrainUI.createRequestState();
  const first = state.begin("opportunity_analysis");
  assert.equal(state.accept(first, { request_type: "opportunity_analysis", analysis_id: 41 }), true);
  assert.equal(state.snapshot().activeBrainRunId, 41);

  state.begin("content_angles");
  assert.deepEqual(state.snapshot(), {
    sequence: 2,
    activeBrainRunId: null,
    activeRequestType: "content_angles",
  });
});

test("an older async response cannot replace the newer selected mode", () => {
  const state = ContentBrainUI.createRequestState();
  const angles = state.begin("content_angles");
  const titles = state.begin("title_hooks");

  assert.equal(state.accept(angles, { request_type: "content_angles", analysis_id: 51 }), false);
  assert.equal(state.accept(titles, { request_type: "title_hooks", analysis_id: 52 }), true);
  assert.equal(state.snapshot().activeBrainRunId, 52);
  assert.equal(state.snapshot().activeRequestType, "title_hooks");
});

test("a response for a different mode is rejected", () => {
  const state = ContentBrainUI.createRequestState();
  const token = state.begin("content_angles");
  assert.throws(
    () => state.accept(token, { request_type: "opportunity_analysis", analysis_id: 60 }),
    /different request type/,
  );
  assert.equal(state.snapshot().activeBrainRunId, null);
});

test("history inspection accepts the stored mode and run without generation", () => {
  const state = ContentBrainUI.createRequestState();
  const token = state.beginHistory(73);
  assert.equal(state.accept(token, { request_type: "longform_outline" }, "longform_outline"), true);
  assert.equal(state.snapshot().activeBrainRunId, 73);
  assert.equal(state.snapshot().activeRequestType, "longform_outline");
});
