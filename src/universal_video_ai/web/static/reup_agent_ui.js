/* Guided channel setup, content choices and a separate video review queue. */
(() => {
  "use strict";
  const el = id => document.getElementById(id);
  if (!el("reup-agent-panel")) return;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const lines = id => el(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  const PLATFORMS = {douyin: "Douyin", tiktok: "TikTok", youtube: "YouTube", bilibili: "Bilibili"};
  const STEPS = {1: "reup-channel-heading", 2: "reup-strategy-heading", 3: "reup-start-heading", 4: "reup-runs-heading"};
  const STATUS = {discovered: "Chờ chọn xử lý", queued: "Đang xếp hàng", localizing: "Đang xử lý video", packaging: "Đang chuẩn bị đăng", ready: "Chờ bạn duyệt", publishing: "Đang tải lên YouTube", processing: "YouTube đang xử lý", scheduled: "Đã lên lịch", uploaded_private: "Đã đăng riêng tư", published: "Đã đăng", failed: "Cần kiểm tra", paused: "Đã tạm dừng", cancelled: "Đã hủy"};
  const GROUPS = {active: ["queued", "localizing", "packaging", "publishing", "processing"], review: ["ready"], done: ["published", "uploaded_private", "scheduled"], error: ["failed"]};
  let profile = {};
  let channel = null;
  let strategy = {status: "not_analyzed", report: null};
  let step = 1;
  let loaded = false;
  let loadVersion = 0;
  let pendingAction = null;
  let strategyTimer = null;
  let runs = [];
  let runFilter = "all";
  let runScope = "latest";
  let currentRunIds = null;
  let runsSignature = "";
  let runRequest = 0;
  let previewRunId = null;
  let shortPlan = null;
  let speakerReadiness = null;
  let reviewBeforeManual = null;
  let sourceTimer = null;
  let sourceSignature = "";
  let sourceOwner = null;
  let sourceRequest = null;
  let sourceFailures = 0;
  let sourceGracePolls = 0;
  const sourceStates = new Map();
  let latestDiscoveryState = null;

  async function sourceApi(path, opts = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(path, {...opts, headers: {"Content-Type": "application/json"}, signal: controller.signal});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const error = new Error([502, 503, 504].includes(response.status)
          ? `Ứng dụng chưa phản hồi (HTTP ${response.status}). Trạng thái sẽ được kiểm tra lại; phiên được giữ, không cần mở đăng nhập lại.`
          : typeof data.detail === "string" ? data.detail : `Không cập nhật được nguồn (HTTP ${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Ứng dụng phản hồi chậm. Phiên và cửa sổ nguồn được giữ; hệ thống sẽ cập nhật trạng thái lại.");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function scheduleSourcePoll(delay = 1500) {
    clearTimeout(sourceTimer);
    sourceTimer = setTimeout(() => {
      sourceTimer = null;
      loadSourceSessions().catch(error => message(error.message, true));
    }, delay);
  }
  function sourceLabel(source) {
    if (source.switching) return "Đang đổi tài khoản nguồn";
    if (source.checking) return "Đang kiểm tra tìm nguồn";
    if (source.access?.status === "searched") return "Nguồn truy cập được";
    if (source.authenticated_hint) return "Đã ghi nhớ đăng nhập";
    if (source.saved) return "Đã lưu phiên truy cập";
    return "Chưa lưu phiên nguồn";
  }
  function renderSourceSessions(sources) {
    const target = el("reup-source-sessions");
    const signature = JSON.stringify(sources);
    if (!target || signature === sourceSignature) return;
    sourceSignature = signature;
    target.innerHTML = sources.map(source => {
      const active = source.browser_open || ["opening", "waiting", "saving"].includes(source.status);
      const waiting = source.status === "opening" || source.status === "saving";
      const attrs = `data-source-platform="${escape(source.platform)}" type="button"`;
      const note = source.access?.message || "";
      const needsVerification = source.access?.status === "verification_required";
      const openSearch = needsVerification || ["no_results", "http_412", "login_required"].includes(source.access?.status);
      const primaryAction = openSearch ? "verify" : "check";
      const primaryLabel = needsVerification ? "Xác minh tìm kiếm" : openSearch ? "Mở trang tìm kiếm" : source.saved ? "Dùng phiên đã lưu" : "Kiểm tra tìm nguồn";
      return `<article class="reup-source-session"><div><strong>${escape(PLATFORMS[source.platform])}</strong><p><span class="reup-status ${source.access?.status === "searched" ? "is-good" : ""}">${escape(sourceLabel(source))}</span></p><p class="reup-help">${escape(source.message)}</p>${note ? `<p class="reup-help">${escape(note)}</p>` : ""}</div><div class="reup-actions">${active
        ? `<button class="btn secondary small" id="reup-source-login-${escape(source.platform)}" ${attrs} data-source-action="login" ${waiting ? "disabled" : ""}>${source.status === "opening" ? "Đang mở cửa sổ…" : "Xem cửa sổ nguồn"}</button><button class="btn secondary small" ${attrs} data-source-action="reload" ${waiting ? "disabled" : ""}>Tải lại cửa sổ nguồn</button><button class="btn gradient small" ${attrs} data-source-action="save" ${waiting ? "disabled" : ""}>Hoàn tất</button>`
        : `<button class="btn gradient small" ${attrs} data-source-action="${primaryAction}" ${source.checking ? "disabled" : ""}>${primaryLabel}</button><button class="btn secondary small" id="reup-source-login-${escape(source.platform)}" ${attrs} data-source-action="login" ${source.checking ? "disabled" : ""}>${source.saved ? "Mở nguồn" : "Đăng nhập nguồn"}</button>${source.authenticated_hint ? `<button class="btn secondary small" ${attrs} data-source-action="switch" ${source.checking ? "disabled" : ""}>Đổi tài khoản</button>` : ""}`}</div></article>`;
    }).join("");
  }
  function openSourceSettings(platform) {
    showStep(1, false);
    const panel = el("reup-source-connections");
    panel.open = true;
    loadSourceSessions().then(() => {
      panel.scrollIntoView({behavior: "smooth", block: "center"});
      el(`reup-source-login-${platform}`)?.focus();
    }).catch(showError);
  }
  async function loadSourceSessions() {
    if (sourceRequest) return sourceRequest;
    clearTimeout(sourceTimer);
    sourceTimer = null;
    sourceRequest = (async () => {
      try {
        const state = await sourceApi("/api/reup-agent/source-sessions");
        sourceFailures = 0;
        const ownerChanged = sourceOwner !== null && state.owner_id != null && sourceOwner !== state.owner_id;
        if (state.owner_id != null && sourceOwner !== state.owner_id) {
          sourceOwner = state.owner_id;
          sourceStates.clear();
          sourceSignature = "";
          if (ownerChanged) {
            latestDiscoveryState = null;
            currentRunIds = null;
            runs = [];
            fill(await api("/api/reup-agent/profile"));
            await Promise.all([loadChannel(), loadStrategy(), refreshRuns(), loadDiscovery()]);
          }
        }
        const sources = state.sources || [];
        for (const source of sources) {
          const previous = sourceStates.get(source.platform);
          if (previous?.browser_open && !previous.video_id && !source.browser_open && source.saved && !source.checking) {
            // Checking metadata never starts processing or publishing jobs.
            Object.assign(source, await sourceApi(`/api/reup-agent/source-sessions/${source.platform}/check`, {method: "POST"}));
          }
          sourceStates.set(source.platform, source);
        }
        renderSourceSessions(sources);
        if (latestDiscoveryState) renderSourceStatus(latestDiscoveryState);
        if (sourceGracePolls > 0) sourceGracePolls--;
        if (sourceGracePolls || sources.some(source => source.browser_open || source.checking || ["opening", "waiting", "saving"].includes(source.status))) scheduleSourcePoll();
      } catch (error) {
        sourceFailures++;
        if (sourceFailures < 3) scheduleSourcePoll(3000);
        throw error;
      }
    })();
    try { return await sourceRequest; }
    finally { sourceRequest = null; }
  }
  el("reup-source-sessions")?.addEventListener("click", event => {
    const button = event.target.closest("[data-source-action]");
    if (!button) return;
    busy(button, async () => {
      sourceGracePolls = 6;
      scheduleSourcePoll();
      try {
        const action = button.dataset.sourceAction;
        const result = await sourceApi(`/api/reup-agent/source-sessions/${button.dataset.sourcePlatform}/${action}`, {method: "POST"});
        message(action === "check" || action === "retry" ? "Đang kiểm tra tìm nguồn bằng phiên hiện tại. Kết quả sẽ tự cập nhật." : result.message);
      } finally {
        await loadSourceSessions();
      }
    });
  });

  function message(text, error = false) {
    const target = el("reup-message");
    target.textContent = text;
    target.classList.toggle("hidden", !text);
    target.classList.toggle("is-error", error);
    target.setAttribute("role", error ? "alert" : "status");
  }
  function formError(text, panel, field) {
    const error = new Error(text);
    error.panel = panel;
    error.field = field;
    return error;
  }
  function showError(error) {
    if (error.panel) showStep(error.panel, false);
    if (error.field) {
      const target = el(error.field);
      let parent = target?.parentElement;
      while (parent && parent !== el("reup-agent-panel")) {
        if (parent.tagName === "DETAILS") parent.open = true;
        parent = parent.parentElement;
      }
      target?.focus();
    }
    message(error.message || String(error), true);
  }
  async function busy(button, operation) {
    button.disabled = true;
    try { await operation(); } catch (error) { showError(error); }
    finally { button.disabled = false; }
  }
  async function wizardAction(name, button, operation) {
    if (pendingAction) return;
    pendingAction = name;
    message("");
    updateControls();
    await busy(button, operation);
    pendingAction = null;
    updateControls();
    renderSummary();
  }
  function selectedPlatforms() {
    return Object.keys(PLATFORMS).filter(platform => el(`reup-${platform}`).checked);
  }
  function selectedDirection() {
    const directions = strategy.report?.directions || [];
    return directions.find(direction => direction.id === el("reup-strategy-direction").value) || directions[0];
  }
  function hasContentChoice() {
    const direction = selectedDirection();
    return strategy.status === "ready" && !!direction && Object.values(direction.search_queries || {}).some(queries => queries.some(query => String(query).trim()));
  }
  function updateControls() {
    const connected = !!channel?.connected;
    const analyzing = !!strategy.running;
    el("reup-analyze").disabled = !connected || !!pendingAction || !!analyzing;
    el("reup-reanalyze").disabled = !connected || !!pendingAction || !!analyzing;
    el("reup-direction-next").disabled = !hasContentChoice() || !!pendingAction || !!analyzing;
    el("reup-direction-next").textContent = el("reup-strategy-direction").value ? "Tiếp tục với chiến lược này →" : "Dùng chiến lược Agent đề xuất →";
    el("reup-scan").disabled = !connected || !hasContentChoice() || !!pendingAction;
    el("reup-search-again").disabled = el("reup-scan").disabled;
    el("reup-search-again").textContent = el("reup-automation").value === "manual" ? "Tìm video lại" : "Tìm và xử lý lại";
    el("reup-save").disabled = !connected || !!pendingAction;
    el("reup-stop-agent").disabled = !profile.enabled || !!pendingAction;
    el("reup-stop-agent").classList.toggle("hidden", !profile.enabled);
    el("reup-profile-state").textContent = profile.enabled ? "Đang chạy định kỳ" : "Chạy theo yêu cầu";
    el("reup-profile-state").classList.toggle("is-enabled", !!profile.enabled);
    document.querySelectorAll("[data-reup-step]").forEach(button => {
      const number = Number(button.dataset.reupStep);
      const complete = number === 1 ? connected : number === 2 ? hasContentChoice() : number === 3 ? runs.length > 0 : false;
      button.classList.toggle("is-complete", complete && number !== step);
    });
  }
  function showStep(next, focus = true) {
    step = Number(next);
    document.querySelectorAll("[data-reup-panel]").forEach(panel => panel.classList.toggle("hidden", Number(panel.dataset.reupPanel) !== step));
    el("reup-short-builder").classList.toggle("hidden", step !== 4);
    document.querySelectorAll("[data-reup-step]").forEach(button => {
      const active = Number(button.dataset.reupStep) === step;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });
    renderSummary();
    updateControls();
    if (focus) {
      el(STEPS[step]).focus({preventScroll: true});
      el("reup-agent-panel").scrollIntoView({behavior: "smooth", block: "start"});
    }
  }
  function navigate(next) {
    message("");
    if ([2, 3].includes(Number(next)) && !channel?.connected) {
      showStep(1);
      message("Đăng nhập kênh YouTube trước khi chọn nội dung.", true);
      return;
    }
    if (Number(next) === 3 && !hasContentChoice()) {
      showStep(2);
      message("Bấm Phân tích kênh & khảo sát trend để Agent đề xuất chiến lược trước khi bắt đầu.", true);
      return;
    }
    showStep(next);
  }
  function updateChannel(info) {
    const changed = channel && (channel.account_ref !== info?.account_ref || channel.connected !== info?.connected);
    channel = info || {connected: false, configured: false};
    el("reup-channel").value = channel.account_ref || "";
    el("reup-channel-name").textContent = channel.connected ? channel.account_name || "Kênh YouTube đã kết nối" : "Chưa kết nối kênh YouTube";
    el("reup-channel-help").textContent = channel.connected
      ? "Đã kết nối · Agent sẽ phân tích và đăng video lên kênh này."
      : channel.configured ? "Đăng nhập bằng tài khoản quản lý kênh của bạn." : "Kết nối YouTube chưa được thiết lập. Mở Tài khoản social để thiết lập hoặc liên hệ quản trị viên.";
    el("reup-channel-card").classList.toggle("is-connected", !!channel.connected);
    el("reup-connect-youtube").textContent = channel.connected ? "Đổi kênh" : "Đăng nhập YouTube";
    el("reup-connect-youtube").disabled = !channel.configured;
    el("reup-open-social-setup").classList.toggle("hidden", !!channel.configured);
    el("reup-connect-next-help").textContent = channel.connected ? "Đã sẵn sàng. Agent sẽ tự kiểm tra kênh có video hay chưa." : "Kết nối kênh để tiếp tục.";
    if (changed) {
      currentRunIds = null;
      runScope = "latest";
      strategy = {status: "not_analyzed", report: null};
      el("reup-strategy-direction").value = "";
      el("reup-package-preview").replaceChildren();
      previewRunId = null;
      renderStrategy(strategy);
      showStep(1, false);
    }
    updateControls();
    renderSummary();
  }
  async function loadChannel() {
    const connections = await api("/api/social/connections");
    updateChannel(connections.youtube);
  }
  function setVoice(id, value) {
    const select = el(id);
    if (![...select.options].some(option => option.value === value)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `Giọng đã lưu · ${value}`;
      select.append(option);
    }
    select.value = value;
  }
  function updateConditionalFields() {
    const mode = el("reup-speakers").value;
    const manual = mode === "manual";
    const autoOption = el("reup-automation").querySelector('option[value="auto"]');
    autoOption.disabled = manual;
    if (manual && el("reup-automation").value === "auto") el("reup-automation").value = "assisted";
    el("reup-voice1-field").classList.toggle("hidden", mode === "single");
    el("reup-short-options").classList.toggle("hidden", !el("reup-short").checked);
    el("reup-short-duration-field").classList.toggle("hidden", !el("reup-short").checked);
    el("reup-schedule-settings").classList.toggle("hidden", el("reup-publish").value !== "schedule");
    el("reup-schedule-zone").textContent = `Theo múi giờ ${el("reup-timezone").value || "Asia/Bangkok"}.`;
    el("reup-automation-help").textContent = ({manual: "Agent chỉ tìm nguồn. Bạn chọn video nào cần xử lý trong bước Theo dõi.", assisted: "Video xử lý xong sẽ nằm trong mục Chờ duyệt. Bạn quyết định có đăng hay không.", auto: "Agent sẽ đăng video sau khi xử lý, theo quyền riêng tư hoặc giờ hẹn bên dưới."})[el("reup-automation").value] || "";
    renderSpeakerReadiness();
    updateControls();
    renderSummary();
  }
  function renderSummary() {
    el("reup-summary-topic").textContent = selectedDirection()?.topic || "Agent sẽ đề xuất từ dữ liệu kênh và social";
    el("reup-summary-channel").textContent = channel?.account_name || (channel?.connected ? "Kênh YouTube đã kết nối" : "Chưa kết nối");
    const searchPlatforms = selectedPlatforms().map(platform => PLATFORMS[platform]).join(", ");
    el("reup-summary-platforms").textContent = searchPlatforms || "Chưa chọn nền tảng khảo sát";
    el("reup-summary-limit").textContent = `${el("reup-limit").value || "1"} video`;
    const automation = el("reup-automation").value;
    const privacy = {private: "Tự đăng riêng tư", immediate: "Tự đăng công khai", schedule: `Tự đăng lúc ${el("reup-time").value}`}[el("reup-publish").value];
    el("reup-summary-action").textContent = automation === "assisted" ? "Chờ bạn duyệt đăng" : automation === "manual" ? "Chờ bạn chọn xử lý" : privacy;
    el("reup-scan").textContent = pendingAction === "scan" ? "Đang tìm video…" : el("reup-enabled").checked ? "Bật Agent và bắt đầu →" : automation === "manual" ? "Tìm video nguồn →" : "Tìm và xử lý video →";
    el("reup-summary-output").textContent = el("reup-enabled").checked ? `Agent tìm nguồn mới mỗi ${el("reup-interval").value} phút, trong giới hạn mỗi ngày.` : "Chạy một lượt. Bạn có thể bật tìm nguồn định kỳ khi cần.";
  }
  function fill(value) {
    profile = value || {};
    el("reup-strategy-direction").value = profile.strategy_direction_id || "";
    const fields = {"reup-market-region": ["market_region", "VN"], "reup-automation": ["automation", "assisted"], "reup-publish": ["publish_mode", "private"], "reup-timezone": ["timezone", "Asia/Bangkok"], "reup-time": ["publish_time", "18:00"], "reup-limit": ["daily_limit", 1], "reup-interval": ["scan_interval_minutes", 60], "reup-speakers": ["speaker_mode", "single"], "reup-short-seconds": ["short_seconds", 60], "reup-model": ["llm_model", ""], "reup-metadata": ["metadata_provider", "none"]};
    for (const [id, [key, fallback]] of Object.entries(fields)) el(id).value = profile[key] ?? fallback;
    el("reup-channel").value = channel?.account_ref || profile.channel_id || "";
    el("reup-exclude").value = (profile.exclude_keywords || []).join("\n");
    for (const platform of Object.keys(PLATFORMS)) el(`reup-${platform}`).checked = (profile.platforms?.length ? profile.platforms : Object.keys(PLATFORMS)).includes(platform);
    el("reup-enabled").checked = !!profile.enabled;
    el("reup-rights").checked = !!profile.rights_confirmed;
    el("reup-short").checked = !!profile.make_short;
    setVoice("reup-voice0", profile.speaker_voices?.speaker_0 || "vi-VN-NamMinhNeural");
    setVoice("reup-voice1", profile.speaker_voices?.speaker_1 || "vi-VN-HoaiMyNeural");
    updateConditionalFields();
  }
  function collectProfile() {
    return {...profile, strategy_mode: "auto", strategy_direction_id: el("reup-strategy-direction").value, market_region: el("reup-market-region").value, topic: "", channel_id: channel?.account_ref || "", keywords: [], exclude_keywords: lines("reup-exclude"), source_urls: [], platforms: selectedPlatforms(), enabled: el("reup-enabled").checked, rights_confirmed: el("reup-rights").checked, automation: el("reup-automation").value, publish_mode: el("reup-publish").value, timezone: el("reup-timezone").value.trim(), publish_time: el("reup-time").value, daily_limit: Number(el("reup-limit").value), scan_interval_minutes: Number(el("reup-interval").value), speaker_mode: el("reup-speakers").value, speaker_voices: {speaker_0: el("reup-voice0").value, speaker_1: el("reup-voice1").value}, make_short: el("reup-short").checked, short_seconds: Number(el("reup-short-seconds").value), metadata_provider: el("reup-metadata").value, llm_model: el("reup-model").value.trim()};
  }
  function validateProfile(value, intent) {
    if (!channel?.connected) throw formError("Đăng nhập kênh YouTube trước khi tiếp tục.", 1, "reup-connect-youtube");
    if (!value.platforms.length) throw formError("Chọn ít nhất một nền tảng để Agent khảo sát trend và tìm video.", 1, "reup-douyin");
    if (intent === "scan" && !hasContentChoice()) throw formError("Phân tích kênh và khảo sát trend trước khi bắt đầu.", 2, "reup-reanalyze");
    if (!Number.isInteger(value.daily_limit) || value.daily_limit < 1 || value.daily_limit > 10) throw formError("Số video mỗi ngày phải từ 1 đến 10.", 3, "reup-limit");
    if (!Number.isInteger(value.scan_interval_minutes) || value.scan_interval_minutes < 15 || value.scan_interval_minutes > 1440) throw formError("Khoảng tìm nguồn phải từ 15 đến 1440 phút.", 3, "reup-interval");
    if (value.automation === "auto" && !value.rights_confirmed) throw formError("Xác nhận quyền sử dụng video nguồn trước khi bật tự đăng.", 3, "reup-rights");
    if (value.automation === "auto" && value.speaker_mode === "manual") throw formError("Gán giọng thủ công cần chọn cách thực hiện có bước duyệt.", 3, "reup-automation");
    if ((value.metadata_provider === "ollama" || value.make_short) && !value.llm_model) throw formError("Nhập model AI đã cài, hoặc tắt tùy chọn dùng AI / tạo video ngắn.", 3, "reup-model");
    try { new Intl.DateTimeFormat("vi-VN", {timeZone: value.timezone}); }
    catch { throw formError("Múi giờ chưa hợp lệ. Ví dụ: Asia/Bangkok.", 3, "reup-timezone"); }
    if (value.publish_mode === "schedule" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(value.publish_time)) throw formError("Chọn giờ công khai video.", 3, "reup-time");
  }
  async function saveProfile(intent = "save") {
    const value = collectProfile();
    validateProfile(value, intent);
    if (value.speaker_mode === "auto" && (intent === "scan" || value.enabled)) {
      await loadSpeakerReadiness();
      if (!speakerReadiness?.ready) throw formError("Nhận diện nhiều người nói chưa sẵn sàng. Chọn một giọng đọc hoặc gán giọng khi duyệt phụ đề.", 3, "reup-speakers");
    }
    const saved = await api("/api/reup-agent/profile", {method: "PUT", body: JSON.stringify(value)});
    fill(saved);
    return saved;
  }
  function evidenceList(items) {
    return `<ul>${items.map(item => `<li><a href="${escape(item.url)}" target="_blank" rel="noopener noreferrer">${escape(item.title || "Xem video làm căn cứ")}</a> · ${escape(PLATFORMS[item.platform] || "YouTube")}${(item.views ?? item.view_count) == null ? "" : ` · ${Number(item.views ?? item.view_count).toLocaleString("vi-VN")} lượt xem`}${item.like_count == null ? "" : ` · ${Number(item.like_count).toLocaleString("vi-VN")} lượt thích`}</li>`).join("")}</ul>`;
  }
  function renderSelectedPlan() {
    const plan = selectedDirection()?.plan;
    el("reup-strategy-plan").classList.toggle("hidden", !plan);
    el("reup-strategy-plan").innerHTML = plan ? `<span class="reup-overline">KẾ HOẠCH AGENT SẼ THỰC HIỆN</span><h4>${escape(plan.goal)}</h4><ol>${(plan.steps || []).map(item => `<li>${escape(item)}</li>`).join("")}</ol><p class="reup-help">Tối đa ${Number(el("reup-limit").value || 1)} video/ngày theo cài đặt chạy. ${escape(plan.measurement || "")}</p>` : "";
  }
  function sourceRecheck(source, state) {
    const session = sourceStates.get(source.platform);
    const access = session?.access;
    const newer = Number(access?.checked_at || 0) > Number(state.completed_at || 0);
    const sameQuery = source.queries?.includes(access?.query);
    const ready = newer && sameQuery && access.status === "searched" && Number(access.count) > 0
      && !["searched", "not_requested", "no_query", "translation_required"].includes(source.status);
    return {session, access, newer, sameQuery, ready};
  }
  function renderSourceStatus(state) {
    latestDiscoveryState = state;
    if (Array.isArray(state.run_ids)) currentRunIds = new Set(state.run_ids);
    else if (Array.isArray(state.runs)) currentRunIds = new Set(state.runs.map(run => run.id));
    else if (state.runs_created === 0) currentRunIds = new Set();
    const sources = (state.platform_status || []).map(source => source.status !== "not_requested" && Array.isArray(source.queries) && !source.queries.some(query => String(query).trim())
      ? {...source, status: "no_query", message: "Chưa gửi truy vấn tìm kiếm. Chuẩn bị lại từ khóa từ nội dung kênh trước khi tìm lại."} : source);
    const warnings = [...new Set(state.warnings || [])];
    el("reup-source-status").classList.toggle("hidden", !sources.length && !warnings.length);
    const labels = {session_changed: "Tài khoản nguồn vừa thay đổi", not_requested: "Không yêu cầu tìm thêm", no_query: "Chưa có từ khóa · chưa tìm kiếm", session_open: "Cửa sổ nguồn đang mở", upstream_error: "Máy chủ nguồn tạm lỗi", timeout: "Nguồn phản hồi chậm", searched: "Đã tìm kiếm", verification_required: "Cần xác minh phiên thu thập", login_required: "Cần đăng nhập nguồn", no_results: "Chưa đọc được kết quả", rate_limited: "Tạm nghỉ do giới hạn tìm kiếm", http_412: "Bị từ chối · HTTP 412", translation_required: "Chưa dịch được từ khóa", browser_unavailable: "Chưa có trình duyệt", unavailable: "Chưa truy cập được"};
    const previous = sources.length && state.query_version !== 4 ? '<p class="reup-help">Kết quả khảo sát trước: bộ từ khóa đã thay đổi. Phân tích lại kênh để dùng truy vấn nội dung mới.</p>' : "";
    el("reup-source-status").innerHTML = `${previous}<div class="reup-source-status-grid">${sources.map(source => {
      const checked = sourceRecheck(source, state);
      const label = checked.ready ? "Có thể tìm lại" : labels[source.status] || source.status;
      return `<article><div class="reup-run-head"><strong>${escape(PLATFORMS[source.platform] || source.platform)}</strong><span class="reup-status ${checked.ready ? "is-good" : source.status === "searched" ? "" : "is-warn"}">${escape(label)}</span></div><p>Từ khóa: ${source.queries?.length ? source.queries.map(escape).join(" · ") : "Chưa có từ khóa phù hợp ngôn ngữ nguồn"}</p><p>${checked.ready ? "Lượt tìm trước: " : ""}${source.retrieved == null ? "" : `Đã nhận ${Number(source.retrieved)} video · `}${Number(source.accepted || 0)} video đủ liên quan${source.rejected ? ` · Loại ${Number(source.rejected)} video` : ""}${source.message ? ` · ${escape(source.message)}` : ""}</p></article>`;
    }).join("")}</div>${warnings.length ? `<details class="reup-help"><summary>Chi tiết của lượt tìm</summary><ul>${warnings.map(warning => `<li>${escape(warning)}</li>`).join("")}</ul></details>` : ""}`;
    for (const [index, source] of sources.entries()) {
      if (["searched", "not_requested"].includes(source.status) || !["douyin", "tiktok", "bilibili"].includes(source.platform)) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn secondary small";
      const {session, access, newer, sameQuery, ready} = sourceRecheck(source, state);
      const needsKeywords = ["no_query", "translation_required"].includes(source.status);
      button.textContent = needsKeywords ? "Xem và chuẩn bị từ khóa" : ready ? el("reup-search-again").textContent : source.status === "verification_required" ? "Xác minh trang tìm kiếm" : session?.saved ? "Kiểm tra nguồn đã lưu" : "Đăng nhập / kiểm tra nguồn";
      if (session) {
        const note = document.createElement("p");
        note.className = "reup-help";
        note.textContent = access?.checked_at
          ? `Kiểm tra nguồn ${newer ? "sau lượt tìm" : "trước lượt tìm"}${access.query ? ` với từ khóa “${access.query}”` : ""}: ${access.message || sourceLabel(session)}${newer && access.status === "searched" && !sameQuery ? " Đây là từ khóa khác với lượt tìm trên." : ""}${ready ? " Kết quả kiểm tra chưa được cộng vào lượt tìm. Tìm lại để cập nhật danh sách video." : ""}`
          : `Phiên nguồn: ${session.message || sourceLabel(session)}. Kết quả lượt tìm được ghi ở trên.`;
        el("reup-source-status").querySelectorAll(".reup-source-status-grid > article")[index]?.append(note);
      }
      button.onclick = () => {
        if (needsKeywords) { showStep(2); loadStrategy().catch(showError); }
        else if (ready) el("reup-search-again").click();
        else if (source.status === "verification_required") {
          busy(button, async () => {
            sourceGracePolls = 6;
            const result = await sourceApi(`/api/reup-agent/source-sessions/${source.platform}/verify`, {method: "POST"});
            message(result.message);
            openSourceSettings(source.platform);
            await loadSourceSessions();
          });
        } else openSourceSettings(source.platform);
      };
      el("reup-source-status").querySelectorAll(".reup-source-status-grid > article")[index]?.append(button);
    }
    if (sources.length) {
      const working = sources.filter(source => source.status === "searched").length;
      const found = sources.reduce((sum, source) => sum + Number(source.accepted || 0), 0);
      const unprepared = sources.filter(source => ["no_query", "translation_required"].includes(source.status)).length;
      const skipped = sources.filter(source => source.status === "not_requested").length;
      const blocked = sources.length - working - unprepared - skipped;
      const rechecked = sources.filter(source => sourceRecheck(source, state).ready).length;
      el("reup-activity-help").textContent = `${found} video phù hợp trong lượt tìm mới nhất. ${working}/${sources.length} nền tảng đã tìm kiếm được${unprepared ? `; ${unprepared} nguồn chưa có truy vấn phù hợp` : ""}${skipped ? `; ${skipped} nguồn không yêu cầu tìm thêm` : ""}${blocked ? `; ${blocked} nguồn chưa truy cập được trong lượt đó` : ""}.${rechecked ? ` ${rechecked} nguồn vừa kiểm tra thành công; có thể tìm lại để cập nhật.` : ""}`;
    }
    renderRuns();
  }
  async function loadDiscovery() { renderSourceStatus(await api("/api/reup-agent/discovery")); }
  function renderStrategy(state) {
    let report = state.report;
    if (report && channel?.account_ref && report.channel?.channel_id !== channel.account_ref) {
      state = {status: "not_analyzed", report: null};
      report = null;
    }
    strategy = state;
    const loading = !!state.running;
    const target = el("reup-strategy-status");
    target.classList.toggle("is-loading", loading);
    el("reup-strategy-result").setAttribute("aria-busy", String(loading));
    if (loading) target.textContent = `Đang phân tích kênh và khảo sát trend social… Đã đọc ${Number(state.videos_scanned || 0).toLocaleString("vi-VN")} video của kênh. Agent sẽ tự chọn cách phân tích theo dữ liệu tìm được.`;
    else if (state.status === "scanning") target.textContent = `Đã lưu tiến độ đọc ${Number(state.videos_scanned || 0).toLocaleString("vi-VN")} video. Bấm Phân tích lại để tiếp tục.`;
    else if (state.status === "error") target.textContent = state.error || "Chưa phân tích xong. Kiểm tra kết nối rồi bấm Phân tích lại.";
    else if (!report) target.textContent = state.message || "Bấm Phân tích lại để Agent đọc kênh, khảo sát social và đề xuất chiến lược.";
    if (state.status !== "ready" || !report) {
      el("reup-strategy-result").replaceChildren();
      el("reup-market-coverage").replaceChildren();
      el("reup-strategy-plan").classList.add("hidden");
      updateConditionalFields();
      return;
    }
    const isNew = report.mode === "new_channel";
    el("reup-strategy-heading").textContent = isNew ? "Chiến lược xây kênh mới" : "Chiến lược phát triển từ nội dung đã đăng";
    target.textContent = isNew ? `Kênh mới chưa có video. Agent đã khảo sát ${report.market_sample_size} video từ các mẫu thị trường để đề xuất hướng khởi đầu.` : `Đã phân tích ${Number(report.videos_analyzed).toLocaleString("vi-VN")} video của kênh và khảo sát nội dung tương tự trên social. Xem căn cứ và trạng thái từng nền tảng bên dưới.`;
    el("reup-market-coverage").textContent = (report.market_coverage || []).map(item => `${PLATFORMS[item.platform] || item.platform}: ${item.sample_size} video khảo sát`).join(" · ");
    const directions = report.directions || [];
    const selected = el("reup-strategy-direction").value;
    if (selected && !directions.some(direction => direction.id === selected)) el("reup-strategy-direction").value = "";
    el("reup-strategy-result").innerHTML = directions.map((direction, index) => {
      const chosen = el("reup-strategy-direction").value ? el("reup-strategy-direction").value === direction.id : index === 0;
      const social = direction.social_evidence || [];
      return `<article class="reup-direction-card ${chosen ? "is-selected" : ""}"><label class="reup-direction-choice"><input type="radio" name="reup-direction-choice" value="${escape(direction.id)}" ${chosen ? "checked" : ""}><span>${index === 0 ? '<span class="reup-recommendation">Agent tự chọn</span>' : '<span class="reup-overline">CHIẾN LƯỢC KHÁC</span>'}<strong>${escape(direction.topic)}</strong><small>${escape(direction.category || "Nội dung phù hợp với kênh")} · ${direction.supporting_videos || 0} video làm căn cứ${social.length ? ` · ${social.length} video social phù hợp` : ""}</small></span></label><details><summary>Vì sao đề xuất chiến lược này?</summary><p>${escape(direction.reason)}</p><p>${isNew ? "Dữ liệu thị trường làm căn cứ" : "Video đã đăng trên kênh của bạn"}</p>${evidenceList(direction.evidence || [])}${social.length ? `<p>Video tương tự tìm được trên social</p>${evidenceList(social)}` : '<p>Agent sẽ tiếp tục tìm video phù hợp khi bạn bắt đầu chạy.</p>'}${Object.values(direction.search_queries || {}).some(queries => queries.length) ? "" : '<p class="reup-help">Chưa có từ khóa nội dung đủ cụ thể. Chọn hướng khác hoặc phân tích lại trước khi chạy.</p>'}<p>Từ khóa Agent tự tạo:<br>${Object.entries(direction.search_queries || {}).map(([platform, queries]) => `${escape(PLATFORMS[platform] || platform)}: ${queries.map(escape).join(", ")}`).join("<br>")}</p></details></article>`;
    }).join("") + (report.warnings || []).map(warning => `<div class="reup-callout reup-research-warning"><p>${escape(warning)}</p></div>`).join("");
    el("reup-strategy-result").querySelectorAll('[name="reup-direction-choice"]').forEach(radio => {
      radio.onchange = () => {
        el("reup-strategy-direction").value = radio.value;
        el("reup-strategy-result").querySelectorAll(".reup-direction-card").forEach(card => card.classList.toggle("is-selected", card.contains(radio)));
        renderSummary();
        updateControls();
        renderSelectedPlan();
      };
    });
    updateConditionalFields();
    renderSelectedPlan();
  }
  function stopStrategyPolling() { clearInterval(strategyTimer); strategyTimer = null; }
  async function loadStrategy() {
    const state = await api("/api/reup-agent/strategy");
    renderStrategy(state);
    if (state.running || state.status === "scanning") startStrategyPolling();
    else stopStrategyPolling();
    return state;
  }
  function startStrategyPolling() {
    if (strategyTimer) return;
    strategyTimer = setInterval(() => {
      if (el("app-view").classList.contains("hidden") || !document.querySelector('.feature-panel[data-feature="reup-agent"]')?.classList.contains("active")) { stopStrategyPolling(); return; }
      loadStrategy().catch(error => {
        stopStrategyPolling();
        renderStrategy({status: "error", report: null, error: `Không tải được tiến độ: ${error.message}. Bấm Phân tích lại để tiếp tục.`});
      });
    }, 3000);
  }
  async function analyze() {
    await saveProfile("analysis");
    renderStrategy({status: "scanning", running: true, videos_scanned: 0, report: null});
    showStep(2);
    try {
      await api("/api/reup-agent/strategy/analyze", {method: "POST"});
      startStrategyPolling();
    } catch (error) {
      renderStrategy({status: "error", report: null, error: error.message});
      throw error;
    }
  }
  function renderRuns() {
    const latest = runScope === "latest" && currentRunIds !== null;
    const scopedRuns = latest ? runs.filter(run => currentRunIds.has(run.id)) : runs;
    el("reup-run-scopes").classList.toggle("hidden", currentRunIds === null);
    el("reup-history-count").textContent = `Toàn bộ lịch sử (${runs.length})`;
    document.querySelectorAll("[data-reup-scope]").forEach(button => {
      const active = button.dataset.reupScope === runScope;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    el("reup-runs-heading").textContent = latest ? "Kết quả lượt tìm mới nhất" : "Video và lịch sử xử lý của bạn";
    for (const [group, id] of Object.entries({active: "reup-stat-active", review: "reup-stat-review", done: "reup-stat-done", error: "reup-stat-error"})) el(id).textContent = scopedRuns.filter(run => GROUPS[group].includes(run.status)).length;
    const pending = runs.filter(run => run.status === "ready").length;
    el("reup-pending-badge").textContent = pending;
    el("reup-pending-badge").classList.toggle("hidden", !pending);
    const visible = scopedRuns.filter(run => runFilter === "all" || GROUPS[runFilter]?.includes(run.status));
    el("reup-run-count").textContent = `${visible.length} / ${scopedRuns.length} video`;
    document.querySelectorAll("[data-reup-filter]").forEach(button => {
      const active = button.dataset.reupFilter === runFilter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (!visible.length) {
      const searching = pendingAction === "scan";
      el("reup-runs").innerHTML = `<div class="reup-empty"><strong>${searching ? "Agent đang tìm video phù hợp…" : latest && !scopedRuns.length ? "Lượt tìm mới nhất chưa có video phù hợp" : scopedRuns.length ? "Không có video trong mục này" : "Chưa có video trong lượt chạy"}</strong>${searching ? "Nguồn tìm được sẽ xuất hiện ở đây, sau đó chuyển sang xử lý." : latest && !scopedRuns.length ? 'Xem trạng thái nguồn ở trên. Các video cũ nằm trong mục “Toàn bộ lịch sử”.' : scopedRuns.length ? "Chọn Tất cả để xem những video còn lại." : "Phân tích kênh rồi bắt đầu tìm nguồn."}${!searching && !scopedRuns.length ? '<br><button type="button" class="btn secondary small" data-reup-empty-start>Phân tích lại kênh →</button>' : ""}</div>`;
      el("reup-runs").querySelector("[data-reup-empty-start]")?.addEventListener("click", () => navigate(channel?.connected ? 2 : 1));
      updateControls();
      return;
    }
    el("reup-runs").innerHTML = visible.map(run => {
      const state = run.status;
      const done = GROUPS.done.includes(state);
      const statusClass = done ? "is-good" : ["ready", "paused"].includes(state) ? "is-warn" : ["failed", "cancelled"].includes(state) ? "is-error" : "";
      const evidence = run.config?.source_evidence;
      const legacySearch = evidence && run.config?.strategy_mode === "auto" && !evidence.relevance;
      const options = [];
      if (state === "discovered" && !legacySearch) options.push(["process", "Xử lý video này"]);
      if (done) options.push(["metrics", "Cập nhật lượt xem"]);
      if (state === "failed" && !legacySearch) options.push(["retry", "Thử lại"]);
      if (state === "failed" && /^https:\/\/(www\.)?douyin\.com\/video\/\d+/.test(run.source_url)) options.unshift(["verify_source", "Kiểm tra video nguồn"]);
      if (state === "paused" && !legacySearch) options.push(["resume", "Tiếp tục"]);
      if (["queued", "localizing", "packaging", "ready"].includes(state)) options.push(["pause", "Tạm dừng"]);
      if (["discovered", "queued", "localizing", "ready", "failed", "paused"].includes(state)) options.push(["cancel", "Hủy video này"]);
      const note = state === "ready" ? "Video đã xử lý xong. Xem trước và duyệt để đăng lên kênh của bạn." : state === "discovered" ? "Bạn quyết định có tải và xử lý nguồn này hay không." : state === "failed" ? "Kiểm tra nguồn hoặc kết nối rồi thử lại." : state === "paused" ? "Luồng đã tạm dừng. Bạn có thể tiếp tục khi sẵn sàng." : "";
      const preview = ["ready", ...GROUPS.done].includes(state) ? `<button type="button" class="btn ${state === "ready" ? "gradient" : "secondary"} small" data-reup-preview="${escape(run.id)}">${state === "ready" ? "Xem video & duyệt đăng" : "Xem video"}</button>` : "";
      const measurement = evidence?.ranking?.method === "metadata_proxy" ? "Ưu tiên tín hiệu lượt xem theo tuổi video, tương tác và độ mới; đây là ước tính từ metadata." : evidence?.ranking?.method === "popularity_only" ? "Có số liệu quan tâm tích lũy; chưa đủ để đo tốc độ tăng trưởng." : "Video phù hợp chủ đề từ mẫu social; chưa đủ số liệu để đo xu hướng tăng trưởng.";
      const matched = evidence?.relevance?.matched_concepts || [];
      const research = evidence ? `${legacySearch ? '<p class="reup-run-error">Lượt cũ chưa qua kiểm tra nội dung, không được chạy lại. Phân tích lại kênh để tìm nguồn mới.</p>' : ""}<details class="reup-source-evidence"><summary>${legacySearch ? "Căn cứ của bộ tìm cũ" : "Vì sao Agent chọn video này?"}</summary><p>${legacySearch ? "Chủ đề đã dùng" : "Phù hợp chiến lược"}: ${escape(run.config.topic || "phát triển kênh")}</p>${matched.length ? `<p>Khớp nội dung: ${matched.map(escape).join(", ")}</p>` : ""}${evidenceList([evidence])}<p>${measurement}</p></details>` : "";
      return `<article class="reup-run-card"><div class="reup-run-head"><strong>${escape(run.refs?.title || run.title || "Video đang chuẩn bị")}</strong><span class="reup-status ${statusClass}">${escape(STATUS[state] || state)}</span></div>${note ? `<p class="reup-help">${note}</p>` : ""}<div class="reup-run-meta"><a href="${escape(run.source_url)}" target="_blank" rel="noopener noreferrer">Xem nguồn gốc ↗</a>${run.refs?.external_url ? `<a href="${escape(run.refs.external_url)}" target="_blank" rel="noopener noreferrer">Mở trên YouTube ↗</a>` : ""}${run.refs?.metrics ? `<span>${escape(run.refs.metrics.views ?? "—")} lượt xem · ${escape(run.refs.metrics.likes ?? "—")} lượt thích</span>` : ""}</div>${research}${run.error ? `<details><summary class="reup-run-error">Chi tiết cần kiểm tra</summary><p class="reup-run-error">${escape(run.error)}</p></details>` : ""}<div class="reup-run-actions">${preview}${options.map(([action, label]) => `<button type="button" class="btn secondary small" data-reup-id="${escape(run.id)}" data-reup-action="${action}">${label}</button>`).join("")}</div></article>`;
    }).join("");
    el("reup-runs").querySelectorAll("[data-reup-action]").forEach(button => {
      button.onclick = () => busy(button, async () => {
        const row = await api(`/api/reup-agent/runs/${encodeURIComponent(button.dataset.reupId)}/${button.dataset.reupAction}`, {method: "POST"});
        if (button.dataset.reupAction === "verify_source") {
          message(row.message || "Đang mở đúng video bằng phiên nguồn đã lưu.");
          sourceGracePolls = 6;
          scheduleSourcePoll();
          openSourceSettings("douyin");
          return;
        }
        message(`Video: ${STATUS[row.status] || row.status}.`);
        await refreshRuns();
      });
    });
    el("reup-runs").querySelectorAll("[data-reup-preview]").forEach(button => {
      button.onclick = () => busy(button, () => previewPackage(button.dataset.reupPreview));
    });
    updateControls();
  }
  async function refreshRuns() {
    const request = ++runRequest;
    const updated = await api("/api/reup-agent/runs");
    if (request !== runRequest) return;
    const signature = JSON.stringify(updated);
    runs = updated;
    if (signature !== runsSignature) {
      runsSignature = signature;
      renderRuns();
    }
  }
  async function previewPackage(runId) {
    const run = runs.find(item => item.id === runId);
    if (!run) return;
    previewRunId = runId;
    const data = await api(`/api/reup-agent/runs/${encodeURIComponent(runId)}/package`);
    if (previewRunId !== runId) return;
    const job = encodeURIComponent(run.job_id || "");
    const video = run.job_id ? (run.refs?.short_id ? `/api/jobs/${job}/shorts/${encodeURIComponent(run.refs.short_id)}/video` : `/api/jobs/${job}/video`) : null;
    const privacy = {private: "Đăng riêng tư", immediate: "Đăng công khai", schedule: "Đăng theo lịch hẹn"}[run.config?.publish_mode] || "Đăng theo cài đặt của video";
    const destinationMatches = !!channel?.connected && (!run.channel_id || run.channel_id === channel.account_ref);
    const ready = run.status === "ready" && destinationMatches;
    el("reup-package-preview").innerHTML = `<div class="reup-run-head"><h4>${escape(data.title)}</h4><button class="btn secondary small" id="reup-preview-close" type="button">Đóng xem trước</button></div>${video ? `<video controls preload="metadata" src="${escape(video)}" style="width:100%;max-height:420px;border-radius:8px;margin-bottom:16px"></video>` : ""}<details><summary class="reup-help">Xem tiêu đề, mô tả và ảnh bìa</summary><img src="${escape(data.thumbnail_url)}" alt="Ảnh bìa video"><p>${escape(data.description)}</p><p>${escape((data.hashtags || []).join(" "))}</p></details>${ready ? `<p class="reup-help">${escape(privacy)} lên kênh ${escape(channel?.account_name || "YouTube đã kết nối")}.</p><label class="reup-consent"><input id="reup-review-rights" type="checkbox" ${profile.rights_confirmed ? "checked" : ""}><span>Tôi xác nhận có quyền sử dụng nguồn video này.</span></label><div class="reup-actions"><button class="btn gradient" id="reup-package-approve" type="button">Duyệt và ${privacy.toLowerCase()}</button></div>` : run.status === "ready" ? '<p class="reup-run-error">Kênh đang kết nối không phải kênh đích của video này. Đổi về đúng kênh để duyệt đăng.</p>' : ""}`;
    el("reup-preview-close").onclick = () => { previewRunId = null; el("reup-package-preview").replaceChildren(); };
    if (ready) el("reup-package-approve").onclick = event => busy(event.currentTarget, async () => {
      if (!el("reup-review-rights").checked) { el("reup-review-rights").focus(); throw new Error("Xác nhận quyền sử dụng nguồn trước khi duyệt đăng video."); }
      if (!profile.rights_confirmed) {
        profile = await api("/api/reup-agent/profile", {method: "PUT", body: JSON.stringify({...profile, rights_confirmed: true})});
        el("reup-rights").checked = true;
      }
      const result = await api(`/api/reup-agent/runs/${encodeURIComponent(runId)}/approve`, {method: "POST"});
      el("reup-package-preview").replaceChildren();
      previewRunId = null;
      message(`Video: ${STATUS[result.status] || result.status}.`);
      await refreshRuns();
    });
    el("reup-package-preview").scrollIntoView({behavior: "smooth", block: "nearest"});
  }
  async function loadWorkspace() {
    if (pendingAction) return;
    const version = ++loadVersion;
    const settings = await api("/api/reup-agent/profile");
    if (version !== loadVersion) return;
    fill(settings);
    await loadChannel();
    if (version !== loadVersion) return;
    await Promise.all([loadStrategy(), refreshRuns(), loadDiscovery(), loadSourceSessions()]);
    if (!loaded) {
      loaded = true;
      showStep(runs.length ? 4 : channel?.connected && hasContentChoice() ? 2 : 1, false);
    }
    if (el("reup-speakers").value !== "single") loadSpeakerReadiness().catch(() => {});
  }
  el("reup-load").onclick = event => busy(event.currentTarget, async () => { await loadWorkspace(); message("Đã cập nhật kênh và tiến độ video."); });
  el("reup-connect-youtube").onclick = () => startConnect("youtube");
  el("reup-open-social-setup").onclick = () => {
    document.querySelector('.feature-tab[data-feature="social-accounts"]')?.click();
    const settings = el("social-app-settings");
    if (settings && !settings.classList.contains("hidden")) settings.open = true;
  };
  el("reup-analyze").onclick = event => wizardAction("analyze", event.currentTarget, analyze);
  el("reup-search-again").onclick = () => el("reup-scan").click();
  el("reup-reanalyze").onclick = event => wizardAction("analyze", event.currentTarget, analyze);
  el("reup-direction-next").onclick = () => navigate(3);
  el("reup-view-settings").onclick = () => navigate(3);
  el("reup-save").onclick = event => wizardAction("save", event.currentTarget, async () => {
    await saveProfile();
    message(profile.enabled ? "Đã lưu cài đặt. Agent đang tìm nguồn định kỳ." : "Đã lưu cài đặt. Bấm Tìm và xử lý video khi bạn muốn bắt đầu.");
  });
  el("reup-stop-agent").onclick = event => wizardAction("stop", event.currentTarget, async () => {
    const saved = await api("/api/reup-agent/profile", {method: "PUT", body: JSON.stringify({...profile, enabled: false})});
    profile = saved;
    el("reup-enabled").checked = false;
    message("Đã tắt tìm nguồn định kỳ. Video đang xử lý vẫn có trong Theo dõi video.");
  });
  el("reup-scan").onclick = event => wizardAction("scan", event.currentTarget, async () => {
    await saveProfile("scan");
    currentRunIds = new Set();
    runScope = "latest";
    showStep(4);
    renderSourceStatus({});
    el("reup-activity-help").textContent = "Agent đang tìm nguồn phù hợp. Video tìm được sẽ xuất hiện bên dưới.";
    renderRuns();
    startStrategyPolling();
    try {
      const result = await api("/api/reup-agent/scan", {method: "POST"});
      message(result.runs.length ? `Đã tìm được ${result.runs.length} video. ${el("reup-automation").value === "manual" ? "Chọn video bạn muốn xử lý bên dưới." : "Agent sẽ xử lý và cập nhật tiến độ tại đây."}` : result.videos_found ? "Có video phù hợp nhưng chưa tạo lượt mới do giới hạn ngày hoặc nguồn đã xử lý. Kiểm tra cài đặt và lịch sử." : "Chưa tìm được video đủ liên quan. Xem từ khóa và trạng thái từng nền tảng bên dưới rồi thử khảo sát lại.");
      el("reup-activity-help").textContent = "Tiến độ được cập nhật tự động. Video đã xong sẽ chờ bạn duyệt nếu chọn chế độ duyệt đăng.";
      renderSourceStatus(result);
      await Promise.all([refreshRuns(), loadStrategy()]);
    } catch (error) {
      stopStrategyPolling();
      el("reup-activity-help").textContent = "Chưa hoàn tất tìm nguồn. Kiểm tra thông báo rồi xem lại cài đặt lượt chạy.";
      throw error;
    } finally {
      el("reup-view-settings").classList.remove("hidden");
      // A late failure must leave an actionable empty state instead of a spinner.
      if (!runs.length) {
        pendingAction = null;
        renderRuns();
      }
    }
  });
  document.querySelectorAll("[data-reup-step]").forEach(button => { button.onclick = () => navigate(button.dataset.reupStep); });
  document.querySelectorAll("[data-reup-back]").forEach(button => { button.onclick = () => navigate(button.dataset.reupBack); });
  document.querySelectorAll("[data-reup-filter]").forEach(button => { button.onclick = () => { runFilter = button.dataset.reupFilter; renderRuns(); }; });
  document.querySelectorAll("[data-reup-scope]").forEach(button => { button.onclick = () => { runScope = button.dataset.reupScope; runFilter = "all"; renderRuns(); }; });
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
  for (const id of ["reup-speakers", "reup-short", "reup-metadata", "reup-publish", "reup-automation", "reup-enabled", "reup-timezone"]) {
    el(id).addEventListener("change", updateConditionalFields);
  }
  for (const id of ["reup-limit", "reup-time", "reup-interval"]) {
    el(id).addEventListener("input", () => { updateControls(); renderSummary(); });
  }
  for (const id of ["reup-market-region", ...Object.keys(PLATFORMS).map(platform => `reup-${platform}`)]) {
    el(id).addEventListener("change", () => {
      renderStrategy({status: "not_analyzed", report: null});
      el("reup-strategy-direction").value = "";
      renderSummary();
    });
  }
  el("reup-speakers").addEventListener("change", () => {
    if (el("reup-speakers").value === "auto") loadSpeakerReadiness().catch(error => message(error.message, true));
  });
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
  document.addEventListener("social-connections-updated", event => updateChannel(event.detail.youtube));
  el("reup-agent-panel").addEventListener("change", () => {
    if (!pendingAction && el("reup-message").classList.contains("is-error")) message("");
  });
  document.querySelector('.feature-tab[data-feature="reup-agent"]')?.addEventListener("click", () => {
    if (el("app-view").classList.contains("hidden")) return;
    // Reopening the tab refreshes progress without replacing in-progress edits.
    const refresh = loaded ? Promise.all([loadChannel(), loadStrategy(), refreshRuns(), loadDiscovery(), loadSourceSessions()]) : loadWorkspace();
    refresh.catch(showError);
    requestAnimationFrame(() => el("reup-agent-panel").scrollIntoView({behavior: "smooth", block: "start"}));
  });
  setInterval(() => {
    if (!el("app-view").classList.contains("hidden") && document.querySelector('.feature-panel[data-feature="reup-agent"]')?.classList.contains("active")) {
      refreshRuns().catch(error => message(`Không tải được tiến độ. ${error.message}`, true));
    }
  }, 8000);
  updateConditionalFields();
})();
