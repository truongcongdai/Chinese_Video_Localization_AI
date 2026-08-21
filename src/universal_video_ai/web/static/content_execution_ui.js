(function contentExecutionUIBootstrap(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ContentExecutionUI = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function contentExecutionUIFactory() {
  "use strict";

  const statusActions = Object.freeze({
    draft: ["review", "approved", "rejected"],
    review: ["draft", "approved", "rejected"],
    approved: ["superseded"],
    rejected: ["draft"],
    superseded: ["approved"],
  });

  function latestByType(assets, assetType) {
    return (assets || []).find(asset => asset.asset_type === assetType) || null;
  }

  function nextMissingSection(sections) {
    return (sections || []).find(row => !row.latest_asset)?.plan?.section_id || null;
  }

  function jobProgress(job) {
    const current = Number(job?.progress_current || 0);
    const total = Math.max(1, Number(job?.progress_total || 1));
    return `${current} / ${total} (${Math.round(current / total * 100)}%)`;
  }

  function generationPath(itemId, type, sectionId) {
    const paths = {
      script_blueprint: `/api/channel-agent/production/${itemId}/script/blueprint`,
      script_resume: `/api/channel-agent/production/${itemId}/script/resume`,
      visual_plan: `/api/channel-agent/production/${itemId}/visual-plan/generate`,
      voice_plan: `/api/channel-agent/production/${itemId}/voice-plan/generate`,
      thumbnail_brief: `/api/channel-agent/production/${itemId}/thumbnail/generate`,
      metadata_package: `/api/channel-agent/production/${itemId}/metadata/generate`,
    };
    if (type === "script_section") {
      return `/api/channel-agent/production/${itemId}/script/sections/${encodeURIComponent(sectionId)}/generate`;
    }
    if (!paths[type]) throw new Error("Unsupported production generation type.");
    return paths[type];
  }

  return Object.freeze({statusActions, latestByType, nextMissingSection, jobProgress, generationPath});
}));
