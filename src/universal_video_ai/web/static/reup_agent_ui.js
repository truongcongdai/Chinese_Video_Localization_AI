/* Source-based automation is independent of the legacy creator UI. */
(() => {
  "use strict";
  const el = id => document.getElementById(id);
  if (!el("reup-agent-panel")) return;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const lines = id => el(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  let profile = {};
  let shortPlan = null;
  let speakerReadiness = null;
  let reviewBeforeManual = null;
  const message = (text, error = false) => {
    const target = el("reup-message");
    target.textContent = text;
    target.classList.toggle("hidden", !text);
    target.classList.toggle("is-error", error);
  };
  async function busy(button, operation) {
    button.disabled = true;
    try { await operation(); } catch (error) { message(error.message || String(error), true); }
    finally { button.disabled = false; }
  }
  function setupMarkup(status) {
    if (status?.ready) return "<strong>Nhận diện tự động đã sẵn sàng</strong>Model local và pyannote.audio đã được tìm thấy. Hãy xem lại phụ đề sau khi xử lý.";
    const steps = [];
    if (!status?.installed) steps.push("Cài thư viện trong môi trường đang chạy app: <code>python -m pip install -r requirements-diarization.txt</code>.");
    if (status?.installed && !status?.audio_decoder_ready) steps.push('TorchCodec chưa đọc được âm thanh. Với Torch CUDA 12.8 trên Linux, cài bản giải mã CPU chính thức: <code>python -m pip install --no-deps --force-reinstall "torchcodec==0.17.0+cpu" --index-url https://download.pytorch.org/whl/cpu</code>.');
    if (!status?.model_ready) steps.push('Chấp nhận điều khoản <a href="https://huggingface.co/pyannote/speaker-diarization-community-1" target="_blank" rel="noopener noreferrer">model Community-1</a>, tải model bằng Git LFS, rồi đặt <code>DIARIZATION_MODEL_PATH</code> trong <code>.env</code> đến thư mục có <code>config.yaml</code>. Khởi động lại app.');
    return `<strong>Chưa thể tự nhận diện người nói</strong><ol>${steps.map(step => `<li>${step}</li>`).join("")}</ol><p>Để xử lý ngay, chọn “Gán khi duyệt phụ đề”.</p>`;
  }
  function renderSpeakerReadiness() {
    for (const [selectId, targetId] of [["reup-speakers", "reup-speaker-readiness"], ["speaker-mode-select", "speaker-auto-readiness"]]) {
      const target = el(targetId);
      const select = el(selectId);
      if (!target || !select) continue;
      const mode = select.value;
      target.classList.toggle("hidden", mode === "single");
      target.classList.toggle("is-ready", mode === "manual" || !!speakerReadiness?.ready);
      if (mode === "manual") target.innerHTML = "<strong>Gán người nói khi duyệt phụ đề</strong>Job sẽ dừng ở bước duyệt để bạn gán người 1 hoặc người 2 cho từng câu trước khi render.";
      if (mode === "auto") target.innerHTML = speakerReadiness ? setupMarkup(speakerReadiness) : "Đang kiểm tra model nhận diện người nói...";
    }
  }
  async function loadSpeakerReadiness() {
    speakerReadiness = await api("/api/speakers/readiness");
    renderSpeakerReadiness();
  }
  function updateConditionalFields() {
    const manualSpeakers = el("reup-speakers").value === "manual";
    const autoOption = el("reup-automation").querySelector('option[value="auto"]');
    autoOption.disabled = manualSpeakers;
    if (manualSpeakers && el("reup-automation").value === "auto") el("reup-automation").value = "assisted";
    el("reup-voice1-field").classList.toggle("hidden", el("reup-speakers").value === "single");
    const needsModel = el("reup-short").checked || el("reup-metadata").value === "ollama";
    el("reup-short-options").classList.toggle("hidden", !needsModel);
    el("reup-short-duration-field").classList.toggle("hidden", !el("reup-short").checked);
    el("reup-schedule-settings").classList.toggle("hidden", el("reup-publish").value !== "schedule");
    el("reup-automation-help").textContent = ({
      manual: "Nguồn mới sẽ chờ bạn bấm Xử lý. Video cũng chờ duyệt trước khi đăng.",
      assisted: "Hệ thống xử lý nguồn, sau đó chờ bạn xem gói đăng và duyệt.",
      auto: "Video sẽ được đăng theo quyền riêng tư và lịch đã chọn sau khi xử lý."
    })[el("reup-automation").value] || "";
    const enabled = el("reup-enabled").checked;
    el("reup-profile-state").textContent = enabled ? "Đang chọn bật quét định kỳ" : "Chưa bật quét định kỳ";
    el("reup-profile-state").classList.toggle("is-enabled", enabled);
    renderSpeakerReadiness();
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
    updateConditionalFields();
  }
  async function refreshRuns() {
    const runs = await api("/api/reup-agent/runs");
    el("reup-run-count").textContent = `${runs.length} nguồn`;
    const statusLabel = {discovered:"Mới tìm thấy", queued:"Đang xếp hàng", localizing:"Đang bản địa hóa",
      packaging:"Đang tạo gói đăng", ready:"Chờ duyệt đăng", publishing:"Đang tải lên",
      processing:"YouTube đang xử lý", scheduled:"Đã hẹn giờ", uploaded_private:"Đã tải lên riêng tư",
      published:"Đã đăng", failed:"Cần xử lý lỗi", paused:"Đã tạm dừng", cancelled:"Đã hủy"};
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
      const preview = ["ready","published","uploaded_private","scheduled"].includes(state) ? `<button type="button" class="btn secondary small" data-reup-preview="${escape(run.id)}">Xem gói đăng</button>` : "";
      return preview + options.map(([action,label]) => `<button type="button" class="btn secondary small" data-reup-id="${escape(run.id)}" data-reup-action="${action}">${label}</button>`).join("");
    };
    el("reup-runs").innerHTML = runs.length ? runs.map(run => {
      const statusClass = ["published","scheduled","uploaded_private"].includes(run.status) ? "is-good" :
        ["failed","cancelled"].includes(run.status) ? "is-error" : ["ready","paused"].includes(run.status) ? "is-warn" : "";
      const meta = [`<a href="${escape(run.source_url)}" target="_blank" rel="noopener noreferrer">Mở video nguồn ↗</a>`,
        run.job_id ? `Job: ${escape(run.job_id)}` : "",
        run.refs?.short_id ? `<a href="/api/jobs/${escape(run.job_id)}/shorts/${escape(run.refs.short_id)}/video" target="_blank" rel="noopener noreferrer">Xem short ↗</a>` : "",
        run.refs?.metrics ? `${escape(run.refs.metrics.views ?? "—")} lượt xem · ${escape(run.refs.metrics.likes ?? "—")} lượt thích` : ""].filter(Boolean).join("");
      return `<article class="reup-run-card"><div class="reup-run-head"><div><strong>${escape(run.title || run.source_key)}</strong><div class="reup-run-meta">${meta}</div></div><span class="reup-status ${statusClass}">${escape(statusLabel[run.status] || run.status)}</span></div>${run.refs?.title ? `<div class="reup-run-meta">Tiêu đề dự kiến: ${escape(run.refs.title)}</div>` : ""}${run.error ? `<p class="reup-run-error">${escape(run.error)}</p>` : ""}<div class="reup-run-actions">${actions(run)}</div></article>`;
    }).join("") : `<div class="reup-empty">Chưa có nguồn nào. Lưu cấu hình rồi chọn “Tìm nguồn ngay”.</div>`;
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
        el("reup-package-preview").innerHTML = `<h4>${escape(data.title)}</h4><img src="${escape(data.thumbnail_url)}" alt="Thumbnail sắp đăng"><p>${escape(data.description)}</p><p>Hashtag: ${escape((data.hashtags || []).join(" "))}</p><p>Tags: ${escape((data.tags || []).join(", "))}</p>`;
      });
    });
  }
  el("reup-load").onclick = event => busy(event.currentTarget, async () => {
    fill(await api("/api/reup-agent/profile"));
    await Promise.all([refreshRuns(), loadSpeakerReadiness()]);
    message("Đã nạp cấu hình và trạng thái người nói.");
  });
  el("reup-save").onclick = event => busy(event.currentTarget, async () => {
    if (el("reup-speakers").value === "auto") await loadSpeakerReadiness();
    const value = {...profile, topic:el("reup-topic").value.trim(), channel_id:el("reup-channel").value.trim(),
      keywords:lines("reup-keywords"), exclude_keywords:lines("reup-exclude"), source_urls:lines("reup-sources"),
      platforms:["youtube","bilibili","douyin"].filter(p => el(`reup-${p}`).checked),
      enabled:el("reup-enabled").checked, rights_confirmed:el("reup-rights").checked,
      automation:el("reup-automation").value, publish_mode:el("reup-publish").value,
      timezone:el("reup-timezone").value.trim(), publish_time:el("reup-time").value,
      daily_limit:Number(el("reup-limit").value), scan_interval_minutes:Number(el("reup-interval").value),
      speaker_mode:el("reup-speakers").value, speaker_voices:{speaker_0:el("reup-voice0").value.trim(),speaker_1:el("reup-voice1").value.trim()},
      metadata_provider:el("reup-metadata").value, make_short:el("reup-short").checked,short_seconds:Number(el("reup-short-seconds").value),llm_model:el("reup-model").value.trim()};
    if (!value.topic) throw new Error("Nhập chủ đề kênh trước khi lưu.");
    if (value.enabled && value.speaker_mode === "auto" && !speakerReadiness?.ready)
      throw new Error("Nhận diện tự động chưa sẵn sàng. Làm theo hướng dẫn ở bước Xử lý video hoặc chọn gán khi duyệt phụ đề.");
    if (value.automation === "auto" && value.speaker_mode === "manual")
      throw new Error("Gán người nói thủ công cần chế độ xử lý có bước duyệt.");
    if ((value.metadata_provider === "ollama" || (value.enabled && value.make_short)) && !value.llm_model)
      throw new Error("Nhập tên model Ollama đã cài để dùng AI cho metadata hoặc short tự động.");
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
  for (const id of ["reup-speakers", "reup-short", "reup-metadata", "reup-publish", "reup-automation", "reup-enabled"])
    el(id).addEventListener("change", updateConditionalFields);
  el("speaker-mode-select")?.addEventListener("change", () => {
    const review = el("review-mode-checkbox");
    if (el("speaker-mode-select").value === "manual") {
      if (reviewBeforeManual === null) reviewBeforeManual = review.checked;
      review.checked = true;
      review.disabled = true;
    } else if (reviewBeforeManual !== null) {
      review.disabled = false;
      review.checked = reviewBeforeManual;
      reviewBeforeManual = null;
    }
    renderSpeakerReadiness();
    if (el("speaker-mode-select").value === "auto") loadSpeakerReadiness().catch(() => {});
  });
  document.querySelector('.feature-tab[data-feature="reup-agent"]')?.addEventListener("click", () => {
    if (!el("app-view").classList.contains("hidden")) el("reup-load").click();
  });
  updateConditionalFields();
})();
