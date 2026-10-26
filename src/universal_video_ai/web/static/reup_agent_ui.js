/* Source-based automation is independent of the legacy creator UI. */
(() => {
  "use strict";
  const el = id => document.getElementById(id);
  if (!el("reup-agent-panel")) return;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const lines = id => el(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  let profile = {};
  let shortPlan = null;
  const message = text => { el("reup-message").textContent = text; };
  async function busy(button, operation) {
    button.disabled = true;
    try { await operation(); } catch (error) { message(error.message || String(error)); }
    finally { button.disabled = false; }
  }
  function fill(value) {
    profile = value || {};
    const fields = {"reup-topic":"topic","reup-channel":"channel_id","reup-automation":"automation",
      "reup-publish":"publish_mode","reup-timezone":"timezone","reup-time":"publish_time",
      "reup-limit":"daily_limit","reup-interval":"scan_interval_minutes","reup-speakers":"speaker_mode",
      "reup-short-seconds":"short_seconds","reup-model":"llm_model","reup-metadata":"metadata_provider"};
    for (const [id, key] of Object.entries(fields)) if (profile[key] !== undefined) el(id).value = profile[key];
    for (const [id, key] of [["reup-keywords","keywords"],["reup-exclude","exclude_keywords"],["reup-sources","source_urls"]]) el(id).value = (profile[key] || []).join("\n");
    for (const platform of ["youtube","bilibili","douyin"]) el(`reup-${platform}`).checked = (profile.platforms || []).includes(platform);
    el("reup-enabled").checked = !!profile.enabled;
    el("reup-rights").checked = !!profile.rights_confirmed;
    el("reup-short").checked = !!profile.make_short;
    el("reup-voice0").value = profile.speaker_voices?.speaker_0 || "vi-VN-NamMinhNeural";
    el("reup-voice1").value = profile.speaker_voices?.speaker_1 || "vi-VN-HoaiMyNeural";
  }
  async function refreshRuns() {
    const runs = await api("/api/reup-agent/runs");
    const actions = run => {
      const state = run.status;
      const options = [];
      if (state === "discovered") options.push(["process","Xử lý"]);
      if (state === "ready") options.push(["approve", "Duyệt đăng & xác nhận quyền nguồn"]);
      if (["published","uploaded_private","scheduled"].includes(state)) options.push(["metrics","Cập nhật số liệu"]);
      if (state === "failed") options.push(["retry","Thử lại"]);
      if (state === "paused") options.push(["resume","Tiếp tục"]);
      if (["queued","localizing","packaging","ready"].includes(state)) options.push(["pause","Dừng sau bước hiện tại"]);
      if (["discovered","queued","localizing","ready","failed","paused"].includes(state)) options.push(["cancel","Hủy luồng"]);
      const preview = ["ready","published","uploaded_private","scheduled"].includes(state) ? `<button class="btn secondary small" data-reup-preview="${escape(run.id)}">Xem gói đăng</button> ` : "";
      return preview + options.map(([action,label]) => `<button class="btn secondary small" data-reup-id="${escape(run.id)}" data-reup-action="${action}">${label}</button>`).join(" ");
    };
    el("reup-runs").innerHTML = runs.length ? `<table><thead><tr><th>Nguồn / Job</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>${runs.map(run => `<tr><td>${escape(run.title || run.source_key)}<br><a href="${escape(run.source_url)}" target="_blank" rel="noopener noreferrer">Xem nguồn</a>${run.job_id ? `<br>Job: ${escape(run.job_id)}` : ""}${run.refs?.short_id ? `<br><a href="/api/jobs/${escape(run.job_id)}/shorts/${escape(run.refs.short_id)}/video" target="_blank" rel="noopener">Xem short</a>` : ""}</td><td>${escape(run.status)}${run.refs?.metrics ? `<br>Lượt xem: ${escape(run.refs.metrics.views ?? "chưa có")} · Like: ${escape(run.refs.metrics.likes ?? "chưa có")}` : ""}${run.refs?.title ? `<br>${escape(run.refs.title)}` : ""}${run.error ? `<br>${escape(run.error)}` : ""}</td><td>${actions(run)}</td></tr>`).join("")}</tbody></table>` : "Chưa có nguồn. Lưu cấu hình rồi tìm nguồn ngay.";
    el("reup-runs").querySelectorAll("[data-reup-action]").forEach(button => {
      button.onclick = () => busy(button, async () => {
        const row = await api(`/api/reup-agent/runs/${button.dataset.reupId}/${button.dataset.reupAction}`, {method:"POST"});
        message(`Trạng thái: ${row.status}. Hủy/dừng luồng không xóa video đã lên YouTube; job bản địa hóa đang chạy vẫn có trong Lịch sử.`);
        await refreshRuns();
      });
    });
    el("reup-runs").querySelectorAll("[data-reup-preview]").forEach(button => {
      button.onclick = () => busy(button, async () => {
        const data = await api(`/api/reup-agent/runs/${button.dataset.reupPreview}/package`);
        el("reup-package-preview").innerHTML = `<div class="card"><h4>${escape(data.title)}</h4><img src="${escape(data.thumbnail_url)}" alt="Thumbnail sắp đăng" style="max-width:320px;width:100%"><p style="white-space:pre-wrap">${escape(data.description)}</p><p>Hashtag: ${escape((data.hashtags || []).join(" "))}</p><p>Tags: ${escape((data.tags || []).join(", "))}</p></div>`;
      });
    });
  }
  el("reup-load").onclick = event => busy(event.currentTarget, async () => {
    fill(await api("/api/reup-agent/profile")); await refreshRuns(); message("Đã nạp cấu hình.");
  });
  el("reup-save").onclick = event => busy(event.currentTarget, async () => {
    const value = {...profile, topic:el("reup-topic").value.trim(), channel_id:el("reup-channel").value.trim(),
      keywords:lines("reup-keywords"), exclude_keywords:lines("reup-exclude"), source_urls:lines("reup-sources"),
      platforms:["youtube","bilibili","douyin"].filter(p => el(`reup-${p}`).checked),
      enabled:el("reup-enabled").checked, rights_confirmed:el("reup-rights").checked,
      automation:el("reup-automation").value, publish_mode:el("reup-publish").value,
      timezone:el("reup-timezone").value.trim(), publish_time:el("reup-time").value,
      daily_limit:Number(el("reup-limit").value), scan_interval_minutes:Number(el("reup-interval").value),
      speaker_mode:el("reup-speakers").value, speaker_voices:{speaker_0:el("reup-voice0").value.trim(),speaker_1:el("reup-voice1").value.trim()},
      metadata_provider:el("reup-metadata").value, make_short:el("reup-short").checked,short_seconds:Number(el("reup-short-seconds").value),llm_model:el("reup-model").value.trim()};
    fill(await api("/api/reup-agent/profile", {method:"PUT",body:JSON.stringify(value)}));
    message("Đã lưu. Chỉ quét định kỳ khi bật cấu hình; chế độ duyệt đăng sẽ chờ bạn duyệt từng video.");
  });
  el("reup-scan").onclick = event => busy(event.currentTarget, async () => {
    message("Đang tìm nguồn...");
    const result = await api("/api/reup-agent/scan", {method:"POST"});
    message(`Có ${result.runs.length} nguồn trong hạn mức. ${(result.warnings || []).join(" ")}`);
    await refreshRuns();
  });
  function collectCuts() {
    if (!shortPlan) throw new Error("Lập timeline trước khi dựng short.");
    const rows = Array.from(el("short-cuts-table").querySelectorAll("[data-cut-row]"));
    return {...shortPlan, cuts: rows.map(row => ({
      source_start:Number(row.querySelector("[data-cut-start]").value),
      source_end:Number(row.querySelector("[data-cut-end]").value),
      reason:row.querySelector("[data-cut-reason]").value,
    }))};
  }
  function renderCuts() {
    el("short-cuts-table").innerHTML = `<table><thead><tr><th>Cảnh</th><th>Bắt đầu (giây)</th><th>Kết thúc (giây)</th><th>Tình tiết</th><th></th></tr></thead><tbody>${shortPlan.cuts.map((cut,i) => `<tr data-cut-row><td>${i+1}</td><td><input type="number" step="0.1" min="0" data-cut-start value="${Number(cut.source_start)}"></td><td><input type="number" step="0.1" min="0" data-cut-end value="${Number(cut.source_end)}"></td><td><input data-cut-reason value="${escape(cut.reason)}"></td><td><button data-cut-preview class="btn secondary small">Xem</button><button data-cut-remove class="btn secondary small">Bỏ</button></td></tr>`).join("")}</tbody></table>`;
    el("short-cuts-table").querySelectorAll("[data-cut-row]").forEach((row,i) => {
      row.querySelector("[data-cut-preview]").onclick = () => {
        el("short-source-preview").currentTime = Number(row.querySelector("[data-cut-start]").value);
        el("short-source-preview").play().catch(() => {});
      };
      row.querySelector("[data-cut-remove]").onclick = () => {
        shortPlan = collectCuts(); shortPlan.cuts.splice(i,1); renderCuts();
      };
    });
  }
  el("short-add-cut").onclick = () => {
    if (!shortPlan) { message("Lập timeline trước để biết độ dài nguồn."); return; }
    shortPlan = collectCuts();
    shortPlan.cuts.push({source_start:0,source_end:Math.min(5,shortPlan.source_duration),reason:""});
    renderCuts();
  };
  el("short-plan-btn").onclick = event => busy(event.currentTarget, async () => {
    const job = encodeURIComponent(el("short-job-id").value.trim());
    const result = await api(`/api/jobs/${job}/short-plan`, {method:"POST",body:JSON.stringify({target_seconds:Number(el("reup-short-seconds").value),llm_model:el("reup-model").value.trim()})});
    shortPlan = result;
    el("short-source-preview").src = `/api/jobs/${job}/video`;
    renderCuts();
    message(result.method === "ai_extractive_summary" ? "AI đã chọn các tình tiết. Xem lại cảnh và thời lượng trước khi dựng." : "Đây là gợi ý highlight theo vị trí. Chọn lại những tình tiết chính trước khi dựng.");
  });
  el("short-render-btn").onclick = event => busy(event.currentTarget, async () => {
    const job = encodeURIComponent(el("short-job-id").value.trim());
    const result = await api(`/api/jobs/${job}/shorts`, {method:"POST",body:JSON.stringify({plan:collectCuts()})});
    const video = document.createElement("video"); video.controls = true; video.src = result.video_url; video.style.maxWidth = "100%";
    el("short-result").replaceChildren(video); message("Đã dựng short riêng; video dài vẫn giữ nguyên trong lịch sử.");
  });
})();
