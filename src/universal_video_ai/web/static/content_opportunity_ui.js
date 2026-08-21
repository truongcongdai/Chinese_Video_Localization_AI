(function contentOpportunityUIBootstrap(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ContentOpportunityUI = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function contentOpportunityUIFactory() {
  "use strict";

  const activeStatuses = "draft,watch,approved";
  const transitions = Object.freeze({
    draft: ["watch", "approved", "rejected"],
    watch: ["draft", "approved", "rejected"],
    approved: ["archived"],
    rejected: ["draft"],
    archived: [],
  });

  function listQuery(filters) {
    const params = new URLSearchParams();
    if (filters.status !== "") params.set("status", filters.status || activeStatuses);
    if (filters.confidence) params.set("confidence", filters.confidence);
    if (filters.competition) params.set("competition", filters.competition);
    if (filters.sourceType) params.set("source_type", filters.sourceType);
    params.set("min_score", String(Math.max(0, Math.min(100, Number(filters.minScore) || 0))));
    params.set("limit", "20");
    return params.toString();
  }

  function scoreRows(breakdown) {
    const labels = {
      trend: "Trend",
      niche_relevance: "Niche relevance",
      candidate_strength: "Candidate strength",
      competitor_evidence: "Competitor evidence",
      pattern_gap_quality: "Pattern/gap quality",
      evidence_confidence: "Evidence confidence",
    };
    const components = breakdown?.components || {};
    return Object.keys(labels).map(key => ({key, label: labels[key], ...(components[key] || {})}));
  }

  function competitionLabel(value) {
    return value === "low" ? "limited observed" : (value || "unknown");
  }

  return Object.freeze({activeStatuses, transitions, listQuery, scoreRows, competitionLabel});
}));
