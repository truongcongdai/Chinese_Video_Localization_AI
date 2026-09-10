(function contentProductionUIBootstrap(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ContentProductionUI = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function contentProductionUIFactory() {
  "use strict";

  const activeStatuses = "queued,planning,ready,in_progress,blocked";
  const itemTransitions = Object.freeze({
    queued: ["planning", "cancelled"],
    planning: ["ready", "blocked", "cancelled"],
    ready: ["in_progress", "blocked", "cancelled"],
    blocked: ["planning", "ready", "cancelled"],
    in_progress: ["completed", "blocked", "cancelled"],
    completed: [], cancelled: [],
  });

  function listQuery(filters) {
    const params = new URLSearchParams();
    params.set("status", filters.status || activeStatuses);
    params.set("min_priority", String(Math.max(0, Math.min(100, Number(filters.minPriority) || 0))));
    if (filters.rights) params.set("rights", filters.rights);
    if (filters.targetFormat) params.set("target_format", filters.targetFormat);
    if (filters.opportunityId != null) params.set("opportunity_id", String(filters.opportunityId));
    params.set("limit", "50");
    return params.toString();
  }

  function progressLabel(progress) {
    const done = Number(progress?.completed_required || 0);
    const total = Number(progress?.total_required || 0);
    return `${done} / ${total} (${Number(progress?.percent || 0)}%)`;
  }

  function taskActions(task) {
    if (task.status === "ready") return task.required ? ["in_progress", "blocked"] : ["in_progress", "blocked", "skipped"];
    if (task.status === "pending") return task.required ? ["blocked"] : ["blocked", "skipped"];
    if (task.status === "in_progress") return ["completed", "blocked"];
    if (task.status === "blocked") return ["ready", "in_progress"];
    return [];
  }

  function latestByType(assets) {
    const latest = {};
    (assets || []).forEach(asset => {
      const key = asset.asset_type + ":" + (asset.asset_key || "");
      if (!latest[key] || Number(asset.version) > Number(latest[key].version)) latest[key] = asset;
    });
    return latest;
  }

  function sectionRows(blueprint, assets) {
    if (!blueprint?.payload?.sections) return [];
    const latest = latestByType((assets || []).filter(asset =>
      asset.asset_type === "script_section"
      && (!blueprint.id || Number(asset.payload?.blueprint_asset_id) === Number(blueprint.id))
    ));
    return blueprint.payload.sections.map(section => {
      const key = "script_section:" + String(section.section_index).padStart(2, "0");
      const asset = latest[key];
      const acceptable = Boolean(asset?.payload?.budget_acceptable);
      const status = !asset
        ? "missing"
        : acceptable && asset.status === "approved"
          ? "approved"
          : acceptable
            ? "draft_acceptable"
            : "partial";
      return {
        ...section,
        assetId: asset?.id || null,
        version: asset?.version || null,
        status,
        acceptable,
        action: !asset ? "generate" : !acceptable ? "resume" : "regenerate",
        actual_words: asset?.payload?.actual_words || 0,
        estimated_duration_minutes: asset?.payload?.estimated_duration_minutes || 0,
        completion_percentage: asset?.payload?.completion_percentage || 0,
      };
    });
  }

  function sectionStatusLabel(section) {
    if (section.status === "draft_acceptable") return "DRAFT/ACCEPTABLE";
    if (section.status === "active") return "GENERATING";
    if (section.status === "failed_retryable") return "FAILED/RETRYABLE";
    if (section.status === "failed_terminal") return "FAILED/TERMINAL";
    return String(section.status || "missing").toUpperCase();
  }

  function readinessLabel(assetPackage) {
    if (!assetPackage) return "Not evaluated";
    return [
      "planning " + (assetPackage.planning_ready ? "ready" : "not ready"),
      "assets " + (assetPackage.asset_ready ? "ready" : "not ready"),
      "QA " + (assetPackage.qa_status || "pending"),
      "rights " + (assetPackage.rights_ready ? "ready" : assetPackage.rights_gate),
    ].join(" | ");
  }

  return Object.freeze({
    activeStatuses, itemTransitions, listQuery, progressLabel, taskActions,
    latestByType, sectionRows, sectionStatusLabel, readinessLabel,
  });
}));
