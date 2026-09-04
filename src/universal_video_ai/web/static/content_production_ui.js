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

  return Object.freeze({activeStatuses, itemTransitions, listQuery, progressLabel, taskActions});
}));
