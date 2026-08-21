(function contentBrainUIBootstrap(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ContentBrainUI = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function contentBrainUIFactory() {
  "use strict";

  const MODES = Object.freeze({
    opportunity_analysis: Object.freeze({ label: "Opportunity Analysis" }),
    content_angles: Object.freeze({ label: "Content Angles" }),
    title_hooks: Object.freeze({ label: "Titles & Hooks" }),
    longform_outline: Object.freeze({ label: "Long-form Outline" }),
  });

  function normalizeMode(value) {
    const mode = String(value || "").trim();
    if (!Object.prototype.hasOwnProperty.call(MODES, mode)) {
      throw new Error("Unsupported Content Brain request type.");
    }
    return mode;
  }

  function buildAnalyzePayload({ mode, selectorType, selectorId, allowLowConfidence }) {
    return {
      request_type: normalizeMode(mode),
      selector_type: String(selectorType || ""),
      selector_id: selectorId == null || selectorId === "" ? null : String(selectorId),
      allow_low_confidence: Boolean(allowLowConfidence),
    };
  }

  function modeView(result, storedRequestType = null) {
    const mode = normalizeMode(result?.request_type || storedRequestType);
    return {
      mode,
      label: MODES[mode].label,
      showOpportunity: mode === "opportunity_analysis",
      showAngles: mode === "content_angles",
      showTitlesHooks: mode === "title_hooks",
      showOutline: mode === "longform_outline",
    };
  }

  function createRequestState() {
    let sequence = 0;
    let activeBrainRunId = null;
    let activeRequestType = null;

    function begin(mode) {
      const normalizedMode = normalizeMode(mode);
      sequence += 1;
      activeBrainRunId = null;
      activeRequestType = normalizedMode;
      return Object.freeze({ sequence, mode: normalizedMode, source: "analysis" });
    }

    function beginHistory(runId) {
      sequence += 1;
      activeBrainRunId = null;
      activeRequestType = null;
      return Object.freeze({ sequence, runId: Number(runId), source: "history" });
    }

    function isCurrent(token) {
      return Boolean(token) && token.sequence === sequence;
    }

    function accept(token, result, storedRequestType = null) {
      if (!isCurrent(token)) return false;
      const mode = normalizeMode(result?.request_type || storedRequestType);
      if (token.mode && token.mode !== mode) {
        throw new Error("Content Brain returned a result for a different request type.");
      }
      const runId = Number(result?.analysis_id ?? result?.run_id ?? token.runId);
      if (!Number.isSafeInteger(runId) || runId <= 0) {
        throw new Error("Content Brain returned an invalid run identifier.");
      }
      activeBrainRunId = runId;
      activeRequestType = mode;
      return true;
    }

    function snapshot() {
      return Object.freeze({ sequence, activeBrainRunId, activeRequestType });
    }

    return Object.freeze({ begin, beginHistory, isCurrent, accept, snapshot });
  }

  return Object.freeze({ MODES, normalizeMode, buildAnalyzePayload, modeView, createRequestState });
}));
