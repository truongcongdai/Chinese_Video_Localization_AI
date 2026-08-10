const $ = (sel) => document.querySelector(sel);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, ch => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[ch]));
let currentJobId = null;
let selectedPlatforms = new Set();
let pollTimer = null;
let pollTimer2 = null;
let selectedTopupPackage = null;
let selectedHistoryJobs = new Set();
let latestResultJobId = null;
let _confirmResolver = null;

function showConfirmDialog(title, message, confirmLabel = "Xóa") {
  $("#confirm-title").textContent = title;
  $("#confirm-message").textContent = message;
  $("#confirm-accept").textContent = confirmLabel;
  $("#confirm-modal").classList.remove("hidden");
  return new Promise(resolve => { _confirmResolver = resolve; });
}

function closeConfirmDialog(accepted) {
  $("#confirm-modal").classList.add("hidden");
  const resolve = _confirmResolver;
  _confirmResolver = null;
  if (resolve) resolve(accepted);
}

$("#confirm-cancel").onclick = () => closeConfirmDialog(false);
$("#confirm-accept").onclick = () => closeConfirmDialog(true);
$("#confirm-modal").addEventListener("click", ev => {
  if (ev.target === $("#confirm-modal")) closeConfirmDialog(false);
});

function showHistoryVideo(job) {
  if (!job || !job.has_video) return;
  $("#latest-result-card").classList.remove("hidden");
  $("#latest-result-title").textContent = job.title || job.source_url;
  $("#latest-improve-btn").dataset.jobId = job.id;
  if (latestResultJobId !== job.id) {
    latestResultJobId = job.id;
    $("#latest-result-video").src = `/api/jobs/${job.id}/video`;
  }
}
const TOPUP_PACKAGES = [
  { credits: 50, amount_vnd: 50000 },
  { credits: 120, amount_vnd: 100000 },
  { credits: 300, amount_vnd: 250000 },
  { credits: 700, amount_vnd: 500000 },
];

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

// ---------------- auth ----------------
let needsRegistrationGlobal = false;
let bootstrapConfig = {};
let currentUserId = null;
let providerSettings = {};

async function initAuth() {
  const boot = await api("/api/bootstrap");
  bootstrapConfig = boot;
  needsRegistrationGlobal = boot.needs_registration;

  if (needsRegistrationGlobal) {
    $("#auth-title").textContent = "Tạo tài khoản admin";
    $("#auth-sub").textContent = "Tài khoản đầu tiên sẽ là quản trị viên hệ thống";
    // Keep both tabs visible even on a brand-new database. The first visit
    // opens registration because an admin must be created, but an existing
    // user can always switch back to Login (for example after changing the
    // local database path or restoring a database).
    $("#show-login-tab").classList.remove("hidden");
    showAuthTab("register");
  }

  // Buttons always stay clickable — this way clicking one always DOES
  // something. If the admin hasn't added that provider's Client ID/Secret
  // to .env yet, the click still goes through to the server, which
  // replies with a clear "here's what to configure" message shown right
  // under the form (instead of silently disabling the button, which just
  // looked broken/unresponsive with no explanation).
  document.querySelectorAll("#social-login-row [data-provider]").forEach(btn => {
    const provider = btn.dataset.provider;
    const configured = !!(boot.identity_providers && boot.identity_providers[provider]);
    if (!configured) btn.title = "Chưa cấu hình — bấm để xem cần thêm gì vào .env";
    btn.onclick = () => startIdentityLogin(provider);
  });

  try {
    const me = await api("/api/me");
    showApp(me);
  } catch {
    showLanding();
  }

  // Surface an error from a failed "Sign in with ..." redirect, if any.
  const params = new URLSearchParams(location.search);
  if (params.get("login_error")) {
    $("#auth-error").textContent = "Đăng nhập thất bại: " + params.get("login_error");
    showAuth();
    history.replaceState(null, "", location.pathname);
  }
  // Someone followed a friend's invite link (?ref=CODE) — prefill it so
  // they don't have to type/paste it manually.
  if (params.get("ref")) {
    $("#register-referral-code").value = params.get("ref");
    showAuthTab("register");
    showAuth();
  }
}

function showLanding() {
  $("#landing-view").classList.remove("hidden");
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.add("hidden");
}

function showAuth() {
  $("#landing-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
  setTimeout(() => $("#login-identifier").focus(), 250);
}

$("#landing-login-btn").addEventListener("click", () => {
  showAuthTab("login");
  showAuth();
});
for (const button of [$("#landing-try-btn"), $("#landing-hero-try-btn"), ...document.querySelectorAll(".landing-cta")]) {
  button.addEventListener("click", () => {
    showAuthTab(needsRegistrationGlobal ? "register" : "login");
    showAuth();
  });
}

function showAuthTab(tab) {
  const isLogin = tab === "login";
  $("#login-form").classList.toggle("hidden", !isLogin);
  $("#register-form").classList.toggle("hidden", isLogin);
  $("#show-login-tab").classList.toggle("active", isLogin);
  $("#show-register-tab").classList.toggle("active", !isLogin);
  $("#auth-title").textContent = isLogin ? "Đăng nhập" : (needsRegistrationGlobal ? "Tạo tài khoản admin" : "Đăng ký");
  $("#auth-error").textContent = "";
}

$("#show-login-tab").onclick = () => showAuthTab("login");
$("#show-register-tab").onclick = () => showAuthTab("register");
$("#login-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  doLogin();
});
$("#register-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  doRegister();
});

async function doLogin() {
  $("#auth-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({
      identifier: $("#login-identifier").value.trim(), password: $("#login-password").value,
    })});
    const me = await api("/api/me");
    showApp(me);
  } catch (e) { $("#auth-error").textContent = e.message; }
}

async function doRegister() {
  $("#auth-error").textContent = "";
  try {
    await api("/api/register", { method: "POST", body: JSON.stringify({
      contact_identifier: $("#register-contact").value.trim(),
      username: $("#register-username").value.trim(),
      password: $("#register-password").value,
      referral_code: $("#register-referral-code").value.trim() || null,
    })});
    const me = await api("/api/me");
    showApp(me);
  } catch (e) { $("#auth-error").textContent = e.message; }
}

async function startIdentityLogin(provider) {
  try {
    const { authorize_url } = await api(`/api/identity/login/${provider}`);
    location.href = authorize_url; // full-page redirect — this IS the login action, not a popup
  } catch (e) { $("#auth-error").textContent = e.message; }
}

function showApp(me) {
  currentUserId = me.id;
  $("#landing-view").classList.add("hidden");
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#userbar-name").textContent = me.username;
  $("#userbar-credits").textContent = `💳 ${me.credits}`;
  $("#admin-btn").classList.toggle("hidden", !me.is_admin);
  $("#feedback-fab").classList.remove("hidden");
  $("#feedback-header-btn").classList.remove("hidden");

  $("#referral-link").textContent = `${location.origin}/?ref=${me.referral_code}`;
  loadLanguages().then(() => {
    const restoredDraft = restoreLocalizationDraft();
    if (!restoredDraft) applyRecommendedLocalizationDefaults();
    updateJobEstimate();
  });
  loadProviderSettings();
  loadPublishingProfiles();
  loadPersonalStats();
  refreshJobQueueStatus();
  requestNotificationPermission();
  refreshJobs();
  pollTimer = setInterval(refreshJobs, 8000);  // Increased from 4000ms to reduce server load
  pollTimer2 = setInterval(refreshMe, 15000);  // Increased from 10000ms
}

$("#referral-copy-btn").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("#referral-link").textContent);
    $("#referral-copy-btn").textContent = "Đã sao chép ✓";
    setTimeout(() => { $("#referral-copy-btn").textContent = "Sao chép link"; }, 1500);
  } catch { /* clipboard API unavailable (e.g. non-HTTPS) — link is still selectable by hand */ }
};

async function loadPersonalStats() {
  try {
    const stats = await api("/api/stats/me");
    const by = stats.by_status || {};
    $("#personal-stats").innerHTML = `
      <div class="stat-box"><div class="num">${stats.total_jobs}</div><div class="label">Tổng số video đã xử lý</div></div>
      <div class="stat-box"><div class="num">${by.done || 0}</div><div class="label">Hoàn tất</div></div>
      <div class="stat-box"><div class="num">${by.error || 0}</div><div class="label">Lỗi</div></div>
      <div class="stat-box"><div class="num">${stats.success_rate != null ? stats.success_rate + "%" : "—"}</div><div class="label">Tỷ lệ thành công</div></div>
    `;
  } catch (e) { /* non-critical widget, fail silently */ }
}

async function refreshJobQueueStatus() {
  if (!$("#job-queue-card")) return;
  try {
    const status = await api("/api/job-queue/status");
    $("#queue-active-count").textContent = status.active || 0;
    $("#queue-waiting-count").textContent = status.queued || 0;
    $("#queue-running-count").textContent = status.running || 0;
    $("#queue-backend-label").textContent = status.backend || "-";
    const redisState = status.redis_client_available ? "Redis client installed" : "Redis client missing";
    $("#job-queue-note").textContent = `${status.phase || "Milestone 6"}: ${redisState}. Web runner: ${status.web_runner}. ${status.web_runner_note || ""}`;
    $("#job-queue-note").style.color = "var(--text-dim)";
  } catch (e) {
    $("#job-queue-note").textContent = `Khong doc duoc trang thai queue: ${e.message}`;
    $("#job-queue-note").style.color = "var(--err)";
  }
}

$("#job-queue-refresh-btn").onclick = refreshJobQueueStatus;

// ---------------- browser notifications ----------------
// Fires a desktop notification when a job finishes (done or error) while
// the tab isn't focused, so the person doesn't have to sit staring at the
// page waiting — this is why refreshJobs() diffs against _lastKnownStatus
// below instead of just re-rendering blindly.
let _lastKnownStatus = {};

function requestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function _notifyJobStatusChange(job) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible") return; // no need to nag while they're already looking
  if (job.status === "done") {
    new Notification("Video đã xử lý xong 🎉", { body: job.title || job.source_url });
  } else if (job.status === "error") {
    new Notification("Xử lý video bị lỗi", { body: job.title || job.source_url });
  }
}

async function refreshMe() {
  try {
    const me = await api("/api/me");
    $("#userbar-credits").textContent = `💳 ${me.credits}`;
    $("#stat-credits").textContent = me.credits;
  } catch (e) { /* session likely expired; next job/history poll will surface it */ }
}

window.addEventListener("message", (ev) => {
  if (ev.data === "social-connected") {
    loadConnections();
  }
});

$("#logout-btn").onclick = async () => {
  await api("/api/logout", { method: "POST" });
  clearInterval(pollTimer);
  location.reload();
};

// ---------------- top-up requests ----------------
function _formatVnd(amount) {
  return new Intl.NumberFormat("vi-VN").format(amount) + "đ";
}

function _topupStatusLabel(status) {
  return { pending: "Đang chờ", approved: "Đã duyệt", rejected: "Từ chối" }[status] || status;
}

function renderTopupPackages() {
  selectedTopupPackage = selectedTopupPackage || TOPUP_PACKAGES[1];
  $("#topup-packages").innerHTML = TOPUP_PACKAGES.map(pkg => `
    <div class="topup-package ${pkg.credits === selectedTopupPackage.credits && pkg.amount_vnd === selectedTopupPackage.amount_vnd ? "active" : ""}"
         data-credits="${pkg.credits}" data-amount="${pkg.amount_vnd}">
      <div class="credits">${pkg.credits} token</div>
      <div class="price">${_formatVnd(pkg.amount_vnd)}</div>
    </div>
  `).join("");
  $("#topup-packages").querySelectorAll(".topup-package").forEach(el => {
    el.onclick = () => {
      selectedTopupPackage = { credits: parseInt(el.dataset.credits, 10), amount_vnd: parseInt(el.dataset.amount, 10) };
      renderTopupPackages();
    };
  });
}

async function loadTopupHistory() {
  const rows = await api("/api/top-up-requests");
  $("#topup-history-body").innerHTML = rows.map(r => `
    <tr>
      <td>${r.credits} token</td>
      <td>${_formatVnd(r.amount_vnd)}</td>
      <td class="status-${r.status}">${_topupStatusLabel(r.status)}</td>
      <td>${new Date(r.created_at * 1000).toLocaleString(uiLocale())}</td>
    </tr>
  `).join("") || `<tr><td colspan="4" style="color:var(--text-dim)">Chưa có yêu cầu nạp nào.</td></tr>`;
}

function renderPayment(payment, amount = 0, content = "") {
  const box = $("#payment-box");
  if (!payment || !payment.configured) {
    box.innerHTML = `<div style="color:var(--warn)">Chưa cấu hình tài khoản nhận tiền. Admin cần thêm PAYMENT_BANK_ID, PAYMENT_ACCOUNT_NUMBER và PAYMENT_ACCOUNT_NAME vào .env rồi khởi động lại web.</div>`;
    return;
  }
  box.innerHTML = `
    <b>Chuyển khoản ngân hàng</b>
    ${payment.qr_url ? `<img src="${escapeHtml(payment.qr_url)}" alt="QR thanh toán">` : ""}
    <div class="payment-line"><span>Ngân hàng</span><b>${escapeHtml(payment.bank_name || payment.bank_id)}</b></div>
    <div class="payment-line"><span>Số tài khoản</span><b>${escapeHtml(payment.account_number)}</b></div>
    <div class="payment-line"><span>Chủ tài khoản</span><b>${escapeHtml(payment.account_name)}</b></div>
    ${amount ? `<div class="payment-line"><span>Số tiền</span><b>${_formatVnd(amount)}</b></div>` : ""}
    ${content ? `<div class="payment-line"><span>Nội dung CK</span><b>${escapeHtml(content)}</b></div>` : `<div style="color:var(--text-dim);font-size:12px;margin-top:8px">Chọn gói và gửi yêu cầu để tạo đúng số tiền, nội dung và mã QR.</div>`}
  `;
}

$("#topup-btn").onclick = async () => {
  $("#topup-status").textContent = "";
  renderTopupPackages();
  $("#topup-modal").classList.remove("hidden");
  try {
    const [payment] = await Promise.all([api("/api/payment-config"), loadTopupHistory()]);
    renderPayment(payment);
  } catch (e) {
    $("#topup-status").style.color = "var(--err)";
    $("#topup-status").textContent = e.message;
  }
};
$("#topup-close").onclick = () => $("#topup-modal").classList.add("hidden");
$("#topup-submit").onclick = async () => {
  $("#topup-status").textContent = "";
  const pkg = selectedTopupPackage || TOPUP_PACKAGES[1];
  try {
    const result = await api("/api/top-up-requests", { method: "POST", body: JSON.stringify({
      credits: pkg.credits,
      amount_vnd: pkg.amount_vnd,
      payment_method: "bank_transfer",
      note: $("#topup-note").value.trim() || null,
    })});
    $("#topup-note").value = "";
    $("#topup-status").style.color = "var(--ok)";
    renderPayment(result.payment, pkg.amount_vnd, result.transfer_content);
    $("#topup-status").textContent = "Đã tạo yêu cầu. Quét QR/chuyển khoản đúng nội dung; admin duyệt xong token sẽ tự cộng.";
    await loadTopupHistory();
  } catch (e) {
    $("#topup-status").style.color = "var(--err)";
    $("#topup-status").textContent = e.message;
  }
};

// ---------------- languages ----------------
async function loadLanguages() {
  try {
    const { targets, sources } = await api("/api/languages");
    const displayNames = typeof Intl.DisplayNames === "function"
      ? new Intl.DisplayNames([uiLocale()], { type: "language", fallback: "code" })
      : null;
    const localizedLabel = language => {
      if (language.code === "auto") return translatedUiText("Tự động phát hiện");
      try { return displayNames?.of(language.code) || language.label; }
      catch { return language.label; }
    };
    const targetOptions = targets.map(l =>
      `<option value="${escapeHtml(l.code)}">${escapeHtml(localizedLabel(l))}</option>`
    ).join("");
    $("#lang-select").innerHTML = targetOptions;
    $("#creator-lang-select").innerHTML = targetOptions;
    $("#source-lang-select").innerHTML = sources.map(l =>
      `<option value="${escapeHtml(l.code)}">${escapeHtml(localizedLabel(l))}</option>`
    ).join("");

    const restoreSelection = (selector, storageKey, fallback) => {
      const select = $(selector);
      const saved = localStorage.getItem(storageKey);
      const requested = saved || fallback;
      select.value = [...select.options].some(option => option.value === requested)
        ? requested
        : fallback;
    };
    restoreSelection("#lang-select", LANGUAGE_STORAGE_KEYS.target, "vi");
    restoreSelection("#creator-lang-select", LANGUAGE_STORAGE_KEYS.creator, $("#lang-select").value || "vi");
    restoreSelection("#source-lang-select", LANGUAGE_STORAGE_KEYS.source, "auto");
    loadVoices();
    loadCreatorVoices();
    loadCreatorCapabilities();
    updateLocalizationSummary();
  } catch (e) { /* keep whatever was already in the selects if this fails */ }
}
document.addEventListener("ui-language-changed", () => {
  if ($("#lang-select")?.options.length) loadLanguages();
});

async function loadVoices() {
  let voices = [];
  const provider = $("#tts-provider-select").value;
  const meta = providerMeta(provider);
  try {
    ({ voices } = await api(`/api/voices?language=${encodeURIComponent($("#lang-select").value)}&provider=${encodeURIComponent(provider)}`));
    $("#voice-select").innerHTML = `<option value="">Mặc định</option>` +
      voices.map(v => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label || v.name || v.id)}</option>`).join("");
  } catch (e) {
    $("#voice-select").innerHTML = `<option value="">Mặc định</option>`;
  }
  const help = $("#voice-select-help");
  if (help) {
    if (provider === "edge") {
      help.textContent = `Free mode: ${voices.length} giọng hệ thống khả dụng theo ngôn ngữ đã chọn.`;
    } else if (meta.connected) {
      help.textContent = `${meta.label || provider}: ${voices.length} giọng/model voice khả dụng từ kết nối đã lưu.`;
    } else {
      help.textContent = `${meta.label || provider} là provider trả phí. Hãy lưu kết nối trước để tải voice/model của tài khoản.`;
    }
  }
  renderVoiceLibrary(voices);
  updateLocalizationSummary();
}
$("#lang-select").addEventListener("change", event => {
  localStorage.setItem(LANGUAGE_STORAGE_KEYS.target, event.target.value);
  loadVoices();
});
$("#tts-provider-select").addEventListener("change", () => {
  updateProviderStatus();
  renderProviderChoices();
  loadVoices();
  scheduleDraftSave();
});
$("#tts-style-select").addEventListener("change", () => {
  updateLocalizationSummary();
  scheduleDraftSave();
});
$("#source-lang-select").addEventListener("change", event => {
  localStorage.setItem(LANGUAGE_STORAGE_KEYS.source, event.target.value);
});

let voicePreviewObjectUrl = null;

function renderVoiceLibrary(voices) {
  const box = $("#voice-library");
  if (!box) return;
  const items = [{ id: "", label: translatedUiText("Mặc định"), gender: "Auto" }, ...voices].slice(0, 10);
  box.innerHTML = items.map((voice, index) => `
    <button class="voice-library-card ${$("#voice-select").value === voice.id ? "active" : ""}"
            type="button" data-library-voice="${escapeHtml(voice.id)}">
      <strong>${index % 2 ? "🎙️" : "🔊"} ${escapeHtml(voice.label)}</strong>
      <small>${escapeHtml(voice.gender || "Neural")} · ${escapeHtml($("#lang-select").selectedOptions[0]?.textContent || "")}</small>
    </button>
  `).join("");
  box.querySelectorAll("[data-library-voice]").forEach(button => {
    button.addEventListener("click", () => {
      $("#voice-select").value = button.dataset.libraryVoice;
      $("#voice-select").dispatchEvent(new Event("change"));
      renderVoiceLibrary(voices);
      previewSelectedVoice();
    });
  });
}

function voicePreviewText() {
  return {
    vi: "Xin chào, đây là giọng đọc bạn đã chọn cho video.",
    en: "Hello, this is the voice selected for your video.",
    zh: "你好，这是你为视频选择的配音。",
    "zh-tw": "你好，這是你為影片選擇的配音。",
    ja: "こんにちは、これは動画用に選択した音声です。",
    ko: "안녕하세요. 동영상에 선택한 음성입니다.",
    th: "สวัสดี นี่คือเสียงที่คุณเลือกสำหรับวิดีโอ",
  }[$("#lang-select").value] || "Hello, this is your selected video voice.";
}

async function previewSelectedVoice() {
  const button = $("#voice-preview-btn");
  const status = $("#voice-preview-status");
  const audio = $("#voice-preview-audio");
  button.disabled = true;
  status.textContent = "Đang tạo bản nghe thử…";
  try {
    const response = await fetch("/api/tts/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: voicePreviewText(),
        language: $("#lang-select").value,
        voice: $("#voice-select").value || null,
        provider: $("#tts-provider-select").value,
        style: $("#tts-style-select").value,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }
    if (voicePreviewObjectUrl) URL.revokeObjectURL(voicePreviewObjectUrl);
    voicePreviewObjectUrl = URL.createObjectURL(await response.blob());
    audio.src = voicePreviewObjectUrl;
    audio.classList.remove("hidden");
    await audio.play();
    status.textContent = "Đang phát demo.";
  } catch (error) {
    status.textContent = `Không nghe thử được: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

$("#voice-preview-btn").addEventListener("click", previewSelectedVoice);
$("#voice-select").addEventListener("change", () => {
  document.querySelectorAll("[data-library-voice]").forEach(card => {
    card.classList.toggle("active", card.dataset.libraryVoice === $("#voice-select").value);
  });
});

function providerMeta(provider) {
  return providerSettings[provider] || {};
}

function updateProviderStatus() {
  const provider = $("#tts-provider-select").value;
  const meta = providerMeta(provider);
  const isFree = provider === "edge";
  const connectLink = $("#provider-connect-link");
  connectLink.classList.toggle("hidden", isFree || !!meta.connected || !meta.connect_url);
  connectLink.href = "#";
  connectLink.textContent = isFree ? "Free" : `Kết nối bằng mã ${meta.label || provider}`;
  if (isFree) {
    $("#tts-provider-status").textContent = "Free mode sẵn sàng: dùng thư viện giọng hệ thống bên dưới, không cần kết nối.";
  } else if (meta.connected) {
    $("#tts-provider-status").textContent = `Đã kết nối ${meta.label || provider}${meta.default_model ? ` · ${meta.default_model}` : ""}.`;
  } else if (meta.status === "planned") {
    $("#tts-provider-status").textContent = `${meta.label || provider} đã có chỗ cấu hình, runtime sẽ cần connector/SDK tương ứng.`;
  } else {
    $("#tts-provider-status").textContent = `Chưa kết nối ${meta.label || provider}. Mở phần provider để lưu mã kết nối một lần, sau đó hệ thống sẽ tải model/voice khả dụng.`;
  }
}

function optionList(values, placeholder) {
  const items = Array.isArray(values) ? values : [];
  return `<option value="">${escapeHtml(placeholder)}</option>` + items.map(item => {
    const value = typeof item === "string" ? item : item.id;
    const label = typeof item === "string" ? item : (item.label || item.name || item.id);
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join("");
}

function renderProviderChoices() {
  const provider = $("#tts-provider-select").value;
  const meta = providerMeta(provider);
  $("#tts-model-select").innerHTML = provider === "edge"
    ? optionList(meta.available_models || ["edge-free-neural"], "Free · Edge neural")
    : optionList(meta.available_models || [], "Theo provider/default");
  const openai = providerMeta("openai");
  const gemini = providerMeta("gemini");
  const ollamaModel = (providerSettings.ollama && providerSettings.ollama.default_model) || "qwen3:1.7b";
  const geminiModel = (gemini.available_llm_models && gemini.available_llm_models[0]) || gemini.default_model || "gemini-3.1-flash-lite";
  const translationMode = $("#translation-mode-select").value;
  let connectProvider = "openai";
  if (translationMode === "faithful") {
    $("#translation-model-select").innerHTML = optionList(["free-segment-translate"], "Dịch sát miễn phí");
    $("#translation-model-select").value = "";
  } else if (translationMode === "gemini") {
    connectProvider = "gemini";
    if (gemini.connected) {
      $("#translation-model-select").innerHTML = optionList(
        gemini.available_llm_models || [geminiModel],
        "Gemini model",
      );
      $("#translation-model-select").value = gemini.default_model || geminiModel;
    } else {
      $("#translation-model-select").innerHTML = optionList([], "Kết nối Gemini để tải model");
      $("#translation-model-select").value = "";
    }
  } else {
    if (openai.connected && (openai.available_llm_models || []).length) {
      $("#translation-model-select").innerHTML = optionList(
        openai.available_llm_models,
        translationMode === "adaptive" ? "Adaptive theo audience" : "LLM theo ngữ cảnh",
      );
    } else {
      $("#translation-model-select").innerHTML = optionList(
        [ollamaModel],
        translationMode === "adaptive" ? "Ollama local · adaptive" : "Ollama local · theo ngữ cảnh",
      );
      $("#translation-model-select").value = ollamaModel;
    }
  }
  const needsConnection = translationMode !== "faithful" && (translationMode === "gemini" ? !gemini.connected : !openai.connected);
  $("#translation-connect-link").classList.toggle("hidden", !needsConnection);
  $("#translation-connect-link").href = "#";
  $("#translation-connect-link").dataset.provider = connectProvider;
  $("#translation-connect-link").textContent = translationMode === "gemini" ? "Kết nối Gemini" : "Tùy chọn: kết nối OpenAI";
  $("#translation-login-status").textContent =
    translationMode === "faithful"
      ? "Dịch sát dùng engine miễn phí, không cần kết nối."
      : (translationMode === "gemini"
        ? (gemini.connected
          ? `Đang dùng Gemini API (${gemini.default_model || geminiModel}).`
          : "Gemini cần API key. Lưu kết nối Gemini để hệ thống kiểm tra key và tải model khả dụng.")
      : (openai.connected
        ? "Đã có kết nối LLM; có thể dịch theo ngữ cảnh/audience."
        : `Không có API key: app sẽ dùng Ollama local CPU (${ollamaModel}). Cần chạy Ollama và pull model trước.`));
}

function renderProviderList() {
  const box = $("#provider-list");
  const providers = Object.values(providerSettings).filter(provider => provider.provider !== "edge");
  box.innerHTML = providers.map(provider => `
    <div class="provider-chip">
      <strong>${escapeHtml(provider.label || provider.provider)}</strong>
      ${provider.connected ? "Đã kết nối" : "Chưa kết nối"} · ${escapeHtml(provider.tier || "")}
      ${provider.default_model ? `<br>Model: ${escapeHtml(provider.default_model)}` : ""}
      ${provider.default_voice ? `<br>Voice: ${escapeHtml(provider.default_voice)}` : ""}
      ${provider.available_models?.length ? `<br>${provider.available_models.length} model khả dụng` : ""}
      ${provider.available_voices?.length ? `<br>${provider.available_voices.length} voice khả dụng` : ""}
      ${provider.api_key_masked ? `<br>Key: ${escapeHtml(provider.api_key_masked)}` : ""}
    </div>
  `).join("") || `<div class="provider-chip"><strong>Free mode</strong>Giọng miễn phí đã sẵn sàng, không cần kết nối.</div>`;
}

function renderSavedProviderSelect() {
  const select = $("#saved-provider-select");
  if (!select) return;
  const connectedProviders = Object.values(providerSettings)
    .filter(provider => provider.provider !== "edge" && provider.connected);
  select.innerHTML = `<option value="">${connectedProviders.length ? "Chọn kết nối đã lưu" : "Chưa có kết nối đã lưu"}</option>` +
    connectedProviders.map(provider => {
      const details = [
        provider.default_model,
        provider.available_voices?.length ? `${provider.available_voices.length} voice` : "",
      ].filter(Boolean).join(" · ");
      const label = `${provider.label || provider.provider}${details ? ` · ${details}` : ""}`;
      return `<option value="${escapeHtml(provider.provider)}">${escapeHtml(label)}</option>`;
    }).join("");
}

async function loadProviderSettings() {
  try {
    const { providers } = await api("/api/provider-settings");
    providerSettings = Object.fromEntries(providers.map(provider => [provider.provider, provider]));
    renderProviderList();
    renderSavedProviderSelect();
    syncProviderSettingsForm();
    updateProviderStatus();
    renderProviderChoices();
  } catch (error) {
    $("#provider-settings-status").textContent = error.message;
  }
}

$("#provider-save-btn").addEventListener("click", async () => {
  $("#provider-settings-status").textContent = "Đang lưu...";
  try {
    await api("/api/provider-settings", {
      method: "POST",
      body: JSON.stringify({
        provider: $("#provider-settings-select").value,
        api_key: $("#provider-api-key-input").value || null,
        api_secret: $("#provider-api-secret-input").value || null,
        default_model: $("#provider-model-select").value || $("#provider-model-input").value || null,
        default_voice: $("#provider-voice-input").value || null,
      }),
    });
    $("#provider-api-key-input").value = "";
    $("#provider-api-secret-input").value = "";
    $("#provider-settings-status").textContent = "Đã lưu provider.";
    await loadProviderSettings();
    loadVoices();
  } catch (error) {
    $("#provider-settings-status").textContent = error.message;
  }
});

$("#provider-delete-btn").addEventListener("click", async () => {
  const provider = $("#provider-settings-select").value;
  $("#provider-settings-status").textContent = "Đang xóa...";
  try {
    await api(`/api/provider-settings/${encodeURIComponent(provider)}`, { method: "DELETE" });
    $("#provider-settings-status").textContent = "Đã xóa provider.";
    await loadProviderSettings();
    loadVoices();
  } catch (error) {
    $("#provider-settings-status").textContent = error.message;
  }
});

function syncProviderSettingsForm() {
  const meta = providerMeta($("#provider-settings-select").value);
  $("#provider-model-select").innerHTML = optionList(meta.available_models || [], "Tự động sau khi kết nối");
  $("#provider-model-select").value = meta.default_model || "";
  $("#provider-model-input").value = "";
  $("#provider-voice-input").value = meta.default_voice || "";
  $("#provider-open-dashboard-link").classList.toggle("hidden", !meta.connect_url);
  $("#provider-open-dashboard-link").href = meta.connect_url || "#";
  $("#provider-open-dashboard-link").textContent = meta.connect_url
    ? `Mở trang tạo/lấy mã ${meta.label || meta.provider}`
    : "Provider này không có trang key";
  $("#provider-settings-status").textContent = meta.connected
    ? `Đã có kết nối ${meta.label || meta.provider}. Bạn có thể chọn ở "Kết nối đã lưu" hoặc cập nhật mã mới tại đây.`
    : `Chưa có kết nối ${meta.label || meta.provider}. Provider không cho app tự đọc key sau login; tạo/copy mã kết nối rồi dán vào ô bên dưới.`;
}

$("#provider-settings-select").addEventListener("change", () => {
  syncProviderSettingsForm();
});

$("#saved-provider-select")?.addEventListener("change", () => {
  const provider = $("#saved-provider-select").value;
  if (!provider) return;
  $("#provider-settings-select").value = provider;
  $("#provider-settings-select").dispatchEvent(new Event("change"));
  $("#tts-provider-select").value = provider;
  updateProviderStatus();
  renderProviderChoices();
  loadVoices();
  scheduleDraftSave();
});

function openProviderConnect(provider, openDashboard = true) {
  $("#provider-settings-select").value = provider;
  $("#provider-settings-select").dispatchEvent(new Event("change"));
  const block = $("#provider-settings-panel");
  block.classList.remove("hidden");
  block.scrollIntoView({ behavior: "smooth", block: "center" });
  const meta = providerMeta(provider);
  if (openDashboard && meta.connect_url) {
    window.open(meta.connect_url, "_blank", "noopener");
  }
  $("#provider-settings-status").textContent = meta.connect_url
    ? (openDashboard
      ? "Trang provider đã mở ở tab mới. Provider không cho hệ thống tự đọc key sau login; hãy tạo/copy mã kết nối, dán vào ô bên dưới rồi bấm Lưu kết nối. Sau khi lưu, hệ thống tự kiểm tra và tải model/voice."
      : "Dán mã kết nối đã tạo từ provider vào ô bên dưới rồi bấm Lưu kết nối. Nếu chưa có mã, bấm nút mở trang provider trong panel này.")
    : "Provider này cần runtime local hoặc cấu hình thủ công.";
  $("#provider-api-key-input").focus();
}

$("#provider-manage-btn").addEventListener("click", event => {
  event.preventDefault();
  const provider = $("#translation-mode-select").value === "gemini"
    ? "gemini"
    : ($("#tts-provider-select").value === "edge" ? "gemini" : $("#tts-provider-select").value);
  openProviderConnect(provider, false);
});

$("#provider-close-btn").addEventListener("click", event => {
  event.preventDefault();
  $("#provider-settings-panel").classList.add("hidden");
});

$("#provider-connect-link").addEventListener("click", event => {
  event.preventDefault();
  const provider = $("#tts-provider-select").value;
  if (provider === "edge") return;
  openProviderConnect(provider, false);
});

$("#provider-open-dashboard-link").addEventListener("click", event => {
  event.preventDefault();
  openProviderConnect($("#provider-settings-select").value);
});
$("#translation-connect-link").addEventListener("click", event => {
  event.preventDefault();
  openProviderConnect($("#translation-connect-link").dataset.provider || "openai", false);
});
$("#translation-mode-select").addEventListener("change", () => {
  renderProviderChoices();
  scheduleDraftSave();
});

async function loadCreatorVoices() {
  try {
    const { voices, language_label, default_voice } = await api(`/api/voices?language=${encodeURIComponent($("#creator-lang-select").value)}`);
    $("#creator-voice-select").innerHTML = `<option value="">Mặc định · ${default_voice}</option>` +
      voices.map(v => `<option value="${v.id}">${v.label}</option>`).join("");
    $("#creator-voice-help").textContent = `${language_label}: ${voices.length} lựa chọn giọng đọc`;
  } catch {
    $("#creator-voice-select").innerHTML = `<option value="">Mặc định theo ngôn ngữ</option>`;
    $("#creator-voice-help").textContent = "Không tải được danh sách giọng đọc";
  }
}

async function loadCreatorCapabilities() {
  const select = $("#creator-image-provider");
  const help = $("#creator-image-provider-help");
  try {
    const data = await api("/api/creator/capabilities");
    const videoOption = select.querySelector('option[value="ai_video"]');
    if (videoOption) {
      videoOption.disabled = !data.ai_video;
      videoOption.textContent = data.ai_video
        ? `AI Sinh Video (${data.ai_video_backend || "GPU"})`
        : "AI Sinh Video (GPU không đủ VRAM)";
    }
    if (!data.ai_video && select.value === "ai_video") select.value = "cpu_ai";
    help.textContent = data.gpu_name
      ? `${data.gpu_name} · ${data.vram_gb} GB VRAM${data.ai_video ? ` · Video: ${data.ai_video_backend}` : " · Chỉ hỗ trợ AI Sinh Ảnh"}`
      : "Không phát hiện GPU CUDA; AI Sinh Ảnh sẽ dùng CPU.";
    help.title = data.ai_video_reason || "";
  } catch (e) {
    help.textContent = "Không kiểm tra được khả năng AI của máy chủ.";
  }
}
$("#creator-lang-select").addEventListener("change", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEYS.creator, $("#creator-lang-select").value);
  await loadCreatorVoices();
  $("#creator-keywords").value = "";
  $("#creator-script").value = "";
  $("#creator-narration").value = "";
  window._creatorSuggestionCache = null;
  window._creatorSuggestionPending = null;
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = "Đã đổi ngôn ngữ. Hãy gen lại nội dung để đồng nhất.";
  updateCreatorSubmitState();
});

// ---------------- global branding ----------------
function getBrandingConfig() {
  const enabled = Boolean($("#branding-enable-checkbox") && $("#branding-enable-checkbox").checked);
  const text = enabled && $("#branding-text-input") ? $("#branding-text-input").value.trim() : "";
  return {
    enabled,
    text,
    preset: $("#branding-preset-select") ? $("#branding-preset-select").value : "balanced",
    edge_runner_enabled: Boolean($("#branding-edge-checkbox") && $("#branding-edge-checkbox").checked),
    diagonal_enabled: !$("#branding-diagonal-checkbox") || $("#branding-diagonal-checkbox").checked,
    pattern_enabled: Boolean($("#branding-pattern-checkbox") && $("#branding-pattern-checkbox").checked),
    fingerprint_enabled: Boolean($("#branding-fingerprint-checkbox") && $("#branding-fingerprint-checkbox").checked),
    avoid_subtitles: !$("#branding-avoid-subtitles-checkbox") || $("#branding-avoid-subtitles-checkbox").checked,
    avoid_center: !$("#branding-avoid-center-checkbox") || $("#branding-avoid-center-checkbox").checked,
  };
}

let publishingProfiles = new Map();
let publishingGenericProfile = null;

function _publishingCsv(selector) {
  const el = $(selector);
  if (!el) return [];
  return String(el.value || "").split(/[,\n]+/).map(v => v.trim()).filter(Boolean);
}

function getPublishingProfileData() {
  return {
    name: $("#publishing-profile-name")?.value.trim() || $("#publishing-channel-name")?.value.trim() || "Hồ sơ tạm",
    channel_name: $("#publishing-channel-name")?.value.trim() || "",
    language: "vi",
    niche: $("#publishing-profile-niche")?.value.trim() || "",
    audience: $("#publishing-profile-audience")?.value.trim() || "",
    brand_line: $("#publishing-profile-brand-line")?.value.trim() || "",
    category: "Entertainment",
    made_for_kids: false,
    default_privacy: "private",
    primary_keyword_groups: { keywords: _publishingCsv("#publishing-profile-keywords") },
    base_tags: _publishingCsv("#publishing-profile-tags"),
    base_hashtags: _publishingCsv("#publishing-profile-hashtags"),
    title_formula: $("#publishing-profile-title-formula")?.value.trim() || "Hook hoặc mâu thuẫn chính + chủ đề cụ thể",
    thumbnail_rules: { max_words: 5, examples: [] },
    description_template: $("#publishing-profile-description-template")?.value || "{episode_summary}\n\n{hashtags}",
  };
}

function getPublishingPackConfig() {
  const enabled = Boolean($("#publishing-enable-checkbox") && $("#publishing-enable-checkbox").checked);
  const platforms = [];
  if (!$("#publishing-youtube-checkbox") || $("#publishing-youtube-checkbox").checked) platforms.push("youtube");
  if ($("#publishing-facebook-checkbox") && $("#publishing-facebook-checkbox").checked) platforms.push("facebook");
  const selectedProfile = $("#publishing-profile-select") ? $("#publishing-profile-select").value : "generic_reup";
  return {
    enabled,
    channel_profile: selectedProfile || "generic_reup",
    channel_name: $("#publishing-channel-name") ? $("#publishing-channel-name").value.trim() : "",
    profile_data: getPublishingProfileData(),
    platforms: platforms.length ? platforms : ["youtube"],
    style: $("#publishing-style-select") ? $("#publishing-style-select").value : "balanced",
    edit_level: $("#publishing-edit-level-select") ? $("#publishing-edit-level-select").value : "balanced",
    provider: $("#publishing-provider-select") ? $("#publishing-provider-select").value : "auto",
    generate_thumbnails: !$("#publishing-thumbnails-checkbox") || $("#publishing-thumbnails-checkbox").checked,
    thumbnail_count: 3,
    generate_publish_ready_video: !$("#publishing-ready-video-checkbox") || $("#publishing-ready-video-checkbox").checked,
    use_publish_ready_for_social_publish: !$("#publishing-use-ready-checkbox") || $("#publishing-use-ready-checkbox").checked,
    playlist_url: $("#publishing-playlist-url") ? $("#publishing-playlist-url").value.trim() || null : null,
    custom_instructions: $("#publishing-custom-instructions") ? $("#publishing-custom-instructions").value.trim() || null : null,
  };
}

function _applyPublishingProfile(entry) {
  const profile = entry?.profile || publishingGenericProfile || {};
  $("#publishing-profile-name").value = entry?.id === "generic_reup" ? "" : (entry?.name || profile.name || "");
  $("#publishing-channel-name").value = profile.channel_name || "";
  $("#publishing-profile-niche").value = profile.niche || "";
  $("#publishing-profile-audience").value = profile.audience || "";
  $("#publishing-profile-brand-line").value = profile.brand_line || "";
  const groups = profile.primary_keyword_groups || {};
  $("#publishing-profile-keywords").value = Object.values(groups).flat().join(", ");
  $("#publishing-profile-tags").value = (profile.base_tags || []).join(", ");
  $("#publishing-profile-hashtags").value = (profile.base_hashtags || []).join(", ");
  $("#publishing-profile-title-formula").value = profile.title_formula || "";
  $("#publishing-profile-description-template").value = profile.description_template || "";
  $("#publishing-profile-default").checked = Boolean(entry?.is_default);
  $("#publishing-profile-delete").classList.toggle("hidden", !entry || entry.id === "generic_reup");
}

async function loadPublishingProfiles(preferredId = null) {
  const select = $("#publishing-profile-select");
  if (!select) return;
  try {
    const data = await api("/api/publishing/profiles");
    publishingProfiles = new Map();
    publishingGenericProfile = data.generic?.profile || null;
    const generic = data.generic || { id: "generic_reup", name: "Reup tổng quát", profile: {} };
    publishingProfiles.set("generic_reup", generic);
    for (const item of (data.profiles || [])) publishingProfiles.set(String(item.id), item);
    select.innerHTML = `<option value="generic_reup">Reup tổng quát · mặc định hệ thống</option>` +
      (data.profiles || []).map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.is_default ? " · mặc định của tôi" : ""}</option>`).join("");
    const defaultProfile = (data.profiles || []).find(item => item.is_default);
    const wanted = preferredId || defaultProfile?.id || "generic_reup";
    select.value = publishingProfiles.has(String(wanted)) ? String(wanted) : "generic_reup";
    _applyPublishingProfile(publishingProfiles.get(select.value));
  } catch (e) {
    $("#publishing-profile-status").textContent = `Không tải được hồ sơ kênh: ${e.message}`;
  }
}

function _publishingProfileRequestBody() {
  const profile = getPublishingProfileData();
  return {
    name: $("#publishing-profile-name").value.trim() || profile.channel_name || "Hồ sơ kênh",
    channel_name: profile.channel_name,
    language: profile.language,
    niche: profile.niche,
    audience: profile.audience,
    brand_line: profile.brand_line,
    category: profile.category,
    made_for_kids: profile.made_for_kids,
    default_privacy: profile.default_privacy,
    keywords: Object.values(profile.primary_keyword_groups || {}).flat(),
    base_tags: profile.base_tags,
    base_hashtags: profile.base_hashtags,
    title_formula: profile.title_formula,
    thumbnail_examples: profile.thumbnail_rules?.examples || [],
    description_template: profile.description_template,
    custom_instructions: $("#publishing-custom-instructions")?.value.trim() || "",
    is_default: Boolean($("#publishing-profile-default")?.checked),
  };
}

if ($("#publishing-enable-checkbox")) {
  $("#publishing-enable-checkbox").onchange = ev => {
    $("#publishing-panel").classList.toggle("hidden", !ev.target.checked);
  };
}
if ($("#publishing-profile-select")) {
  $("#publishing-profile-select").onchange = ev => {
    _applyPublishingProfile(publishingProfiles.get(String(ev.target.value)) || publishingProfiles.get("generic_reup"));
    $("#publishing-profile-status").textContent = "";
  };
}
if ($("#publishing-profile-new")) {
  $("#publishing-profile-new").onclick = () => {
    $("#publishing-profile-select").value = "generic_reup";
    _applyPublishingProfile({ id: "generic_reup", profile: publishingGenericProfile || {} });
    $("#publishing-profile-name").value = "";
    $("#publishing-channel-name").value = "";
    $("#publishing-profile-status").textContent = "Điền thông tin rồi bấm Lưu hồ sơ.";
    $("#publishing-profile-editor").open = true;
  };
}
if ($("#publishing-profile-save")) {
  $("#publishing-profile-save").onclick = async () => {
    const selected = $("#publishing-profile-select").value;
    const body = _publishingProfileRequestBody();
    if (!body.channel_name) {
      $("#publishing-profile-status").textContent = "Hãy nhập tên kênh trước khi lưu.";
      return;
    }
    try {
      const saved = selected && selected !== "generic_reup"
        ? await api(`/api/publishing/profiles/${encodeURIComponent(selected)}`, { method: "PUT", body: JSON.stringify(body) })
        : await api("/api/publishing/profiles", { method: "POST", body: JSON.stringify(body) });
      $("#publishing-profile-status").textContent = "Đã lưu hồ sơ riêng cho tài khoản này ✓";
      await loadPublishingProfiles(saved.id);
    } catch (e) {
      $("#publishing-profile-status").textContent = e.message;
    }
  };
}
if ($("#publishing-profile-delete")) {
  $("#publishing-profile-delete").onclick = async () => {
    const selected = $("#publishing-profile-select").value;
    if (!selected || selected === "generic_reup") return;
    const ok = await showConfirmDialog("Xóa hồ sơ kênh", "Chỉ xóa hồ sơ đã lưu. Các job cũ vẫn giữ snapshot SEO của chính job đó.", "Xóa");
    if (!ok) return;
    try {
      await api(`/api/publishing/profiles/${encodeURIComponent(selected)}`, { method: "DELETE" });
      await loadPublishingProfiles();
      $("#publishing-profile-status").textContent = "Đã xóa hồ sơ.";
    } catch (e) {
      $("#publishing-profile-status").textContent = e.message;
    }
  };
}


if ($("#branding-enable-checkbox")) {
  $("#branding-enable-checkbox").onchange = (ev) => {
    $("#branding-panel").classList.toggle("hidden", !ev.target.checked);
  };
}
if ($("#content-os-branding-enable")) {
  $("#content-os-branding-enable").onchange = (ev) => {
    $("#content-os-branding-panel").classList.toggle("hidden", !ev.target.checked);
  };
}
if ($("#creator-branding-enable")) {
  $("#creator-branding-enable").onchange = (ev) => {
    $("#creator-branding-panel").classList.toggle("hidden", !ev.target.checked);
  };
}

function getCreatorBrandingConfig() {
  const enabled = Boolean($("#creator-branding-enable") && $("#creator-branding-enable").checked);
  return {
    enabled,
    text: enabled ? $("#creator-branding-text").value.trim() : "",
    preset: $("#creator-branding-preset") ? $("#creator-branding-preset").value : "balanced",
    edge_runner_enabled: true,
    diagonal_enabled: true,
    pattern_enabled: true,
    fingerprint_enabled: true,
    avoid_subtitles: true,
    avoid_center: true,
  };
}

// ---------------- logo overlay ----------------
let uploadedLogoId = null;

$("#logo-enable-checkbox").onchange = (ev) => {
  $("#logo-panel").classList.toggle("hidden", !ev.target.checked);
};

document.querySelectorAll(".corner-opt").forEach(el => {
  el.onclick = () => {
    document.querySelectorAll(".corner-opt").forEach(o => o.classList.remove("active"));
    el.classList.add("active");
  };
});

$("#logo-file-input").onchange = async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  $("#logo-upload-status").textContent = "Đang tải lên...";
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch("/api/upload-logo", { method: "POST", body: form });
    if (!resp.ok) throw new Error((await resp.json()).detail || "Tải logo thất bại");
    const data = await resp.json();
    uploadedLogoId = data.logo_path;
    $("#logo-preview-box").classList.remove("hidden");
    $("#logo-preview-img").src = data.preview_url;
    $("#logo-upload-status").textContent = "Đã tải lên ✓";
  } catch (e) {
    $("#logo-upload-status").textContent = e.message;
    uploadedLogoId = null;
  }
};

// ---------------- remix flow ----------------
function getRemixPlatforms() {
  return Array.from(document.querySelectorAll(".remix-platform-checkbox:checked")).map(cb => cb.value);
}

async function updateRemixPlanPreview() {
  if (!$("#remix-enable-checkbox") || !$("#remix-enable-checkbox").checked) return;
  const platforms = getRemixPlatforms();
  try {
    const plan = await api("/api/remix/plan", { method: "POST", body: JSON.stringify({
      remix_enabled: true,
      remix_platforms: platforms,
      remix_goal: $("#remix-goal-select").value,
      remix_strength: $("#remix-strength-select").value,
      free_mode: true,
    })});
    $("#remix-plan-preview").textContent =
      `Flow: ${plan.primary_format} · ${plan.processing_mode} · ${plan.translation_mode} · ` +
      `${plan.pipeline_steps.length} bước · QA ${plan.qa_checks.length} mục · không watermark hệ thống.`;
  } catch (e) {
    $("#remix-plan-preview").textContent = "Chưa lấy được remix plan.";
  }
}

function syncRemixPanel() {
  const checkbox = $("#remix-enable-checkbox");
  const panel = $("#remix-panel");
  if (!checkbox || !panel) return;
  panel.classList.toggle("hidden", !checkbox.checked);
  if (checkbox.checked) {
    updateRemixPlanPreview();
  }
}

$("#remix-enable-checkbox")?.addEventListener("change", syncRemixPanel);
$("#remix-enable-checkbox")?.addEventListener("click", () => setTimeout(syncRemixPanel, 0));
syncRemixPanel();

document.querySelectorAll(".remix-platform-checkbox").forEach(cb => {
  cb.onchange = () => {
    cb.closest(".corner-opt")?.classList.toggle("active", cb.checked);
    updateRemixPlanPreview();
  };
});

["#remix-goal-select", "#remix-strength-select"].forEach(selector => {
  const el = $(selector);
  if (el) el.onchange = updateRemixPlanPreview;
});

// ---------------- animated subtitles ----------------
$("#animated-subtitle-checkbox").onchange = (ev) => {
  $("#animated-subtitle-panel").classList.toggle("hidden", !ev.target.checked);
};

function getAnimatedSubtitleConfig() {
  if (!$("#animated-subtitle-checkbox").checked) {
    return null;
  }
  return {
    enabled: true,
    effect: $("#subtitle-effect-select").value,
    style: {
      font_size: parseInt($("#subtitle-font-size").value, 10),
      font_color: $("#subtitle-font-color").value,
      background_color: $("#subtitle-bg-color").value,
    },
    effect_params: {
      glow_color: $("#subtitle-glow-color").value,
      gradient_colors: $("#subtitle-gradient-colors").value,
    },
  };
}

// ---------------- advanced script options ----------------
$("#advanced-script-options-checkbox").onchange = (ev) => {
  $("#advanced-script-options-panel").classList.toggle("hidden", !ev.target.checked);
};

function getAdvancedScriptOptions() {
  if (!$("#advanced-script-options-checkbox").checked) {
    return null;
  }
  return {
    style: $("#script-style-select").value,
    tone: $("#script-tone-select").value,
    sentence_length: $("#script-sentence-length").value,
    detail_level: $("#script-detail-level").value,
    custom_instructions: $("#script-custom-instructions").value.trim() || null,
  };
}

// ---------------- youtube tools ----------------
$("#youtube-tools-checkbox").onchange = (ev) => {
  $("#youtube-tools-panel").classList.toggle("hidden", !ev.target.checked);
};

function useYouTubeInPipeline() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui long nhap URL YouTube");
    return;
  }
  const input = $("#url-input");
  const existing = input.value.trim();
  input.value = existing ? `${existing}\n${url}` : url;
  $("#youtube-result").style.display = "block";
  $("#youtube-result").innerHTML = `<div style="color:var(--ok)">Da dua URL vao pipeline chinh. Hay chon ngon ngu/voice roi bam Bat dau xu ly.</div>`;
  input.focus();
  input.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function downloadYouTubeVideo() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui lòng nhập URL YouTube");
    return;
  }
  
  const resultDiv = $("#youtube-result");
  resultDiv.style.display = "block";
  resultDiv.innerHTML = "Đang tải video...";
  
  try {
    const response = await fetch("/api/youtube/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format: "best" }),
    });
    const data = await response.json();
    
    if (data.ok) {
      resultDiv.innerHTML = `
        <div style="color:var(--ok)">✓ Download thành công!</div>
        <div style="margin-top:8px"><strong>Tên file:</strong> ${data.filename}</div>
        <div><strong>Tiêu đề:</strong> ${data.title}</div>
        <div><strong>Thời lượng:</strong> ${data.duration}s</div>
      `;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${data.detail || "Download thất bại"}</div>`;
    }
  } catch (err) {
    resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${err.message}</div>`;
  }
}

async function extractYouTubeAudio() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui lòng nhập URL YouTube");
    return;
  }
  
  const resultDiv = $("#youtube-result");
  resultDiv.style.display = "block";
  resultDiv.innerHTML = "Đang extract audio...";
  
  try {
    const response = await fetch("/api/youtube/audio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    
    if (data.ok) {
      resultDiv.innerHTML = `
        <div style="color:var(--ok)">✓ Extract audio thành công!</div>
        <div style="margin-top:8px"><strong>Tên file:</strong> ${data.filename}</div>
        <div><strong>Tiêu đề:</strong> ${data.title}</div>
        <div><strong>Thời lượng:</strong> ${data.duration}s</div>
      `;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${data.detail || "Extract audio thất bại"}</div>`;
    }
  } catch (err) {
    resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${err.message}</div>`;
  }
}

async function downloadYouTubeSubtitles() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui lòng nhập URL YouTube");
    return;
  }
  
  const resultDiv = $("#youtube-result");
  resultDiv.style.display = "block";
  resultDiv.innerHTML = "Đang tải subtitles...";
  
  try {
    const response = await fetch("/api/youtube/subtitles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    
    if (data.ok) {
      const subsList = data.subtitles.map(s => 
        `<div>• ${s.language}: <a href="${s.url}" target="_blank" style="color:var(--accent)">Download</a></div>`
      ).join("");
      resultDiv.innerHTML = `
        <div style="color:var(--ok)">✓ Tìm thấy ${data.subtitles.length} subtitles!</div>
        <div style="margin-top:8px"><strong>Tiêu đề:</strong> ${data.title}</div>
        <div style="margin-top:8px">${subsList}</div>
      `;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${data.detail || "Download subtitles thất bại"}</div>`;
    }
  } catch (err) {
    resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${err.message}</div>`;
  }
}

async function downloadYouTubeThumbnail() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui lòng nhập URL YouTube");
    return;
  }
  
  const resultDiv = $("#youtube-result");
  resultDiv.style.display = "block";
  resultDiv.innerHTML = "Đang tải thumbnail...";
  
  try {
    const response = await fetch("/api/youtube/thumbnail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    
    if (data.ok) {
      resultDiv.innerHTML = `
        <div style="color:var(--ok)">✓ Download thumbnail thành công!</div>
        <div style="margin-top:8px"><strong>Tên file:</strong> ${data.filename}</div>
        <div><strong>URL:</strong> <a href="${data.url}" target="_blank" style="color:var(--accent)">${data.url}</a></div>
      `;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${data.detail || "Download thumbnail thất bại"}</div>`;
    }
  } catch (err) {
    resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${err.message}</div>`;
  }
}

async function getYouTubeMetadata() {
  const url = $("#youtube-url").value.trim();
  if (!url) {
    alert("Vui lòng nhập URL YouTube");
    return;
  }
  
  const resultDiv = $("#youtube-result");
  resultDiv.style.display = "block";
  resultDiv.innerHTML = "Đang lấy metadata...";
  
  try {
    const response = await fetch("/api/youtube/metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    
    if (data.title) {
      resultDiv.innerHTML = `
        <div style="color:var(--ok)">✓ Metadata:</div>
        <div style="margin-top:8px"><strong>Tiêu đề:</strong> ${data.title}</div>
        <div><strong>Uploader:</strong> ${data.uploader}</div>
        <div><strong>Thời lượng:</strong> ${data.duration}s</div>
        <div><strong>Lượt xem:</strong> ${data.view_count}</div>
        <div><strong>Upload date:</strong> ${data.upload_date}</div>
        <div><strong>Tags:</strong> ${data.tags.slice(0, 10).join(", ")}</div>
        <div style="margin-top:8px"><strong>Thumbnail:</strong> <a href="${data.thumbnail}" target="_blank" style="color:var(--accent)">Xem</a></div>
      `;
    } else {
      resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: Không lấy được metadata</div>`;
    }
  } catch (err) {
    resultDiv.innerHTML = `<div style="color:var(--err)">✗ Lỗi: ${err.message}</div>`;
  }
}

// ---------------- video templates ----------------
$("#video-template-checkbox").onchange = (ev) => {
  $("#video-template-panel").classList.toggle("hidden", !ev.target.checked);
};

// Audio filter range sliders
$("#audio-eq-bass").oninput = (ev) => {
  $("#audio-eq-bass-value").textContent = ev.target.value;
};

$("#audio-eq-treble").oninput = (ev) => {
  $("#audio-eq-treble-value").textContent = ev.target.value;
};

function getVideoTemplateConfig() {
  if (!$("#video-template-checkbox").checked) {
    return null;
  }
  
  const audio_filters = {};
  
  // Equalizer
  const bass = parseInt($("#audio-eq-bass").value);
  const treble = parseInt($("#audio-eq-treble").value);
  if (bass !== 0 || treble !== 0) {
    audio_filters.equalizer = {};
    if (bass !== 0) audio_filters.equalizer.bass = bass;
    if (treble !== 0) audio_filters.equalizer.treble = treble;
  }
  
  // Normalize
  if ($("#audio-normalize-checkbox").checked) {
    audio_filters.normalize = true;
  }
  
  // Compressor
  if ($("#audio-compressor-checkbox").checked) {
    audio_filters.compressor = {
      threshold: -20,
      ratio: 4,
      attack: 20,
      release: 250
    };
  }
  
	  return {
	    enabled: true,
	    template: $("#video-template-select").value,
	    target_aspect_ratio: $("#output-aspect-ratio").value,
	    transition: $("#template-transition").value,
    color_effect: $("#template-color-effect").value,
    video_quality: $("#template-video-quality").value,
    audio_filters: audio_filters,
  };
}

// ---------------- video transformations ----------------
$("#video-transform-checkbox").onchange = (ev) => {
  $("#video-transform-panel").classList.toggle("hidden", !ev.target.checked);
};

function getTransformConfig() {
  const aspect = $("#output-aspect-ratio").value;
  if (!$("#video-transform-checkbox").checked && (aspect === "source" || aspect === "auto")) {
    return null;
  }
  
  const config = {
    enabled: true,
    enable_flip: $("#transform-flip-enable").checked,
    flip_mode: $("#transform-flip-mode").value,
    enable_border: $("#transform-border-enable").checked,
    border_position: $("#transform-border-position").value,
    border_px: parseInt($("#transform-border-px").value, 10),
    border_color: $("#transform-border-color").value,
    enable_split_screen: $("#transform-split-enable").checked,
    split_mode: $("#transform-split-mode").value,
    enable_randomization: $("#transform-random-enable").checked,
    crop_percent: parseFloat($("#transform-crop-percent").value),
    speed_factor: parseFloat($("#transform-speed-factor").value),
    brightness_adjust: parseFloat($("#transform-brightness").value),
    contrast_adjust: parseFloat($("#transform-contrast").value),
    target_aspect_mode: aspect === "auto" ? "auto" : "fixed",
  };
  const dimensions = {
    "9:16": [1080, 1920],
    "16:9": [1920, 1080],
    "1:1": [1080, 1080],
  }[aspect];
  if (dimensions) {
    [config.target_width, config.target_height] = dimensions;
  }
  
  // Only include overlay path if split screen is enabled
  if (config.enable_split_screen && $("#transform-overlay-path").value.trim()) {
    config.overlay_path = $("#transform-overlay-path").value.trim();
  }
  
  return config;
}

// ---------------- onboarding wizard ----------------
let currentOnboardingStep = 1;
const totalOnboardingSteps = 4;

function showOnboardingStep(step) {
  // Hide all steps
  document.querySelectorAll('.onboarding-step').forEach(el => {
    el.classList.remove('active');
  });
  
  // Show current step
  const stepEl = document.querySelector(`.onboarding-step[data-step="${step}"]`);
  if (stepEl) {
    stepEl.classList.add('active');
  }
  
  // Update progress dots
  const dots = document.querySelectorAll('.onboarding-progress-dot');
  dots.forEach((dot, index) => {
    dot.classList.remove('active', 'completed');
    if (index + 1 === step) {
      dot.classList.add('active');
    } else if (index + 1 < step) {
      dot.classList.add('completed');
    }
  });
  
  currentOnboardingStep = step;
}

function nextOnboardingStep() {
  if (currentOnboardingStep < totalOnboardingSteps) {
    showOnboardingStep(currentOnboardingStep + 1);
  }
}

function prevOnboardingStep() {
  if (currentOnboardingStep > 1) {
    showOnboardingStep(currentOnboardingStep - 1);
  }
}

function skipOnboarding() {
  const overlay = document.getElementById('onboarding-overlay');
  if (overlay) {
    overlay.classList.add('hidden');
  }
  localStorage.setItem('onboarding_completed', 'true');
}

function finishOnboarding() {
  skipOnboarding();
}

function checkOnboarding() {
  const completed = localStorage.getItem('onboarding_completed');
  if (!completed) {
    const overlay = document.getElementById('onboarding-overlay');
    if (overlay) {
      overlay.classList.remove('hidden');
    }
  }
}

// Check onboarding on page load
document.addEventListener('DOMContentLoaded', () => {
  checkOnboarding();
});

// ---------------- jobs ----------------
let currentLocalizationStep = 1;

function extractVideoUrlsFromText(text) {
  const matches = String(text || "").match(/https?:\/\/[^\s<>'"]+/g) || [];
  return matches.map(url => url.replace(/[.,;:!?)[\]}>】》”’"']+$/g, ""));
}

function channelModeEnabled() {
  return Boolean($("#channel-mode-checkbox")?.checked);
}

function updateChannelModeUI() {
  const enabled = channelModeEnabled();
  $("#channel-options-panel").classList.toggle("hidden", !enabled);
  $("#url-input").rows = enabled ? 1 : 2;
  $("#url-input").placeholder = enabled
    ? "https://www.youtube.com/@kenh/videos hoặc https://www.tiktok.com/@user hoặc https://www.douyin.com/user/..."
    : "https://www.youtube.com/watch?v=...\nhttps://v.douyin.com/...\nhttps://www.tiktok.com/@user/video/...";
  $("#channel-analysis-status").textContent = enabled ? "Chưa quét." : "Chưa quét.";
  updateLocalizationSummary();
}

function localizationUrls() {
  const entries = [];
  for (const line of $("#url-input").value.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const extracted = extractVideoUrlsFromText(trimmed);
    if (extracted.length) entries.push(...extracted);
    else entries.push(trimmed);
  }
  return [...new Set(entries)];
}

function updateLocalizationSummary() {
  const urls = localizationUrls();
  const source = $("#source-lang-select")?.selectedOptions[0]?.textContent || "—";
  const target = $("#lang-select")?.selectedOptions[0]?.textContent || "—";
  const voice = $("#voice-select")?.selectedOptions[0]?.textContent || translatedUiText("Mặc định");
  if (channelModeEnabled()) {
    $("#url-count-help").textContent = urls.length
      ? `${urls.length} link kênh đã nhập; chế độ này chỉ chấp nhận đúng 1 link kênh.`
      : "Chưa có link kênh.";
    $("#summary-video-count").textContent = urls.length ? "1 kênh" : "0 kênh";
  } else {
    $("#url-count-help").textContent = urls.length
      ? `${urls.length} link video sẽ được xử lý.`
      : "Chưa có link video.";
    $("#summary-video-count").textContent = `${urls.length} link`;
  }
  $("#summary-language").textContent = `${source} → ${target}`;
  $("#summary-voice").textContent = voice;
}

function validateLocalizationStep(step) {
  if (step !== 1) return true;
  const urls = localizationUrls();
  if (!urls.length) {
    $("#submit-error").textContent = channelModeEnabled()
      ? "Nhập đúng 1 đường link kênh YouTube, TikTok hoặc Douyin."
      : "Nhập ít nhất 1 đường link video.";
    $("#url-input").focus();
    return false;
  }
  if (channelModeEnabled() && urls.length !== 1) {
    $("#submit-error").textContent = "Chế độ tải toàn bộ kênh chỉ nhận đúng 1 link kênh mỗi lần.";
    $("#url-input").focus();
    return false;
  }
  const invalid = urls.find(value => {
    try { return !["http:", "https:"].includes(new URL(value).protocol); }
    catch { return true; }
  });
  if (invalid) {
    $("#submit-error").textContent = `Không tìm thấy link video hợp lệ trong: ${invalid}`;
    $("#url-input").focus();
    return false;
  }
  $("#submit-error").textContent = "";
  return true;
}

function showLocalizationStep(step, validateCurrent = false) {
  if (validateCurrent && step > currentLocalizationStep && !validateLocalizationStep(currentLocalizationStep)) return;
  currentLocalizationStep = Math.max(1, Math.min(3, step));
  document.querySelectorAll("[data-localization-panel]").forEach(panel => {
    panel.classList.toggle("active", Number(panel.dataset.localizationPanel) === currentLocalizationStep);
  });
  document.querySelectorAll("[data-localization-step]").forEach(tab => {
    const tabStep = Number(tab.dataset.localizationStep);
    tab.classList.toggle("active", tabStep === currentLocalizationStep);
    tab.classList.toggle("completed", tabStep < currentLocalizationStep);
  });
  updateLocalizationSummary();
}

document.querySelectorAll("[data-localization-next]").forEach(button => {
  button.addEventListener("click", () => showLocalizationStep(Number(button.dataset.localizationNext), true));
});
document.querySelectorAll("[data-localization-back]").forEach(button => {
  button.addEventListener("click", () => showLocalizationStep(Number(button.dataset.localizationBack)));
});
document.querySelectorAll("[data-localization-step]").forEach(button => {
  button.addEventListener("click", () => {
    const requested = Number(button.dataset.localizationStep);
    showLocalizationStep(requested, requested > currentLocalizationStep);
  });
});
$("#url-input").addEventListener("input", updateLocalizationSummary);
$("#channel-mode-checkbox").addEventListener("change", updateChannelModeUI);
$("#channel-max-videos").addEventListener("input", updateLocalizationSummary);
$("#channel-skip-existing").addEventListener("change", updateLocalizationSummary);
updateChannelModeUI();

function setChannelScanButtonsDisabled(disabled) {
  for (const selector of [
    "#channel-analyze-btn",
    "#channel-continue-btn",
    "#channel-deep-scan-btn",
    "#channel-reset-scan-btn",
  ]) {
    const button = $(selector);
    if (button) button.disabled = disabled;
  }
}

function formatChannelScanStatus(result) {
  const total = Number(result.catalog_total ?? result.video_count ?? 0);
  const fresh = Number(result.new_video_count ?? 0);
  const current = Number(result.current_scan_count ?? 0);
  const state = result.complete
    ? "đã quét hết theo tín hiệu phân trang"
    : (result.has_more === true ? "vẫn còn video" : "chưa xác nhận đã hết");
  const pieces = [
    `Catalog: ${total} video`,
    `lượt này thấy ${current}`,
    `mới ${fresh}`,
    state,
  ];
  if (result.channel_title) pieces.push(result.channel_title);
  if (result.stop_reason) pieces.push(`dừng: ${result.stop_reason}`);
  const warning = (result.warnings || []).join(" ");
  return pieces.join(" · ") + (warning ? ` · ${warning}` : "");
}

async function scanChannel(mode) {
  $("#submit-error").textContent = "";
  const urls = localizationUrls();
  if (!channelModeEnabled() || urls.length !== 1) {
    $("#submit-error").textContent = "Bật chế độ tải toàn bộ kênh và dán đúng 1 link kênh trước khi quét.";
    return;
  }
  setChannelScanButtonsDisabled(true);
  const labels = {
    quick: "Đang quét nhanh danh sách công khai...",
    continue: "Đang tiếp tục cuộn để tìm video chưa có trong catalog...",
    deep: "Đang quét sâu toàn bộ kênh; thao tác này có thể mất vài phút...",
    reset: "Đang xóa catalog cũ và quét lại từ đầu...",
  };
  $("#channel-analysis-status").textContent = labels[mode] || labels.quick;
  try {
    const result = await api("/api/channel-downloads/analyze", {
      method: "POST",
      body: JSON.stringify({
        url: urls[0],
        max_videos: parseInt($("#channel-max-videos").value, 10) || 0,
        mode,
      }),
    });
    $("#channel-analysis-status").textContent = formatChannelScanStatus(result);
  } catch (error) {
    $("#channel-analysis-status").textContent = `Quét thất bại: ${error.message}`;
  } finally {
    setChannelScanButtonsDisabled(false);
  }
}

$("#channel-analyze-btn").addEventListener("click", () => scanChannel("quick"));
if ($("#channel-continue-btn")) {
  $("#channel-continue-btn").addEventListener("click", () => scanChannel("continue"));
}
if ($("#channel-deep-scan-btn")) {
  $("#channel-deep-scan-btn").addEventListener("click", () => scanChannel("deep"));
}
if ($("#channel-reset-scan-btn")) {
  $("#channel-reset-scan-btn").addEventListener("click", () => scanChannel("reset"));
}
for (const selector of ["#source-lang-select", "#lang-select", "#voice-select"]) {
  $(selector).addEventListener("change", updateLocalizationSummary);
}
for (const selector of ["#processing-mode-select", "#translation-mode-select", "#translation-tone-select", "#translation-audience-input", "#translation-glossary-input"]) {
  $(selector).addEventListener("change", updateLocalizationSummary);
  $(selector).addEventListener("input", scheduleJobEstimate);
}

const LOCALIZATION_DRAFT_VERSION = 1;
let draftTimer = null;
let estimateTimer = null;

function localizationDraftKey() {
  return `localization-draft:v${LOCALIZATION_DRAFT_VERSION}:${currentUserId || "guest"}`;
}

function localizationDraftControls() {
  return [...document.querySelectorAll(
    '[data-localization-panel] input[id]:not([type="file"]), ' +
    '[data-localization-panel] select[id], [data-localization-panel] textarea[id]'
  )];
}

function saveLocalizationDraft() {
  if (!currentUserId) return;
  const values = {};
  for (const control of localizationDraftControls()) {
    values[control.id] = ["checkbox", "radio"].includes(control.type) ? control.checked : control.value;
  }
  localStorage.setItem(localizationDraftKey(), JSON.stringify({
    saved_at: Date.now(), step: currentLocalizationStep, values,
  }));
  $("#draft-status").textContent = `Đã lưu ${new Date().toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" })}`;
}

function scheduleDraftSave() {
  clearTimeout(draftTimer);
  $("#draft-status").textContent = "Đang lưu bản nháp…";
  draftTimer = setTimeout(saveLocalizationDraft, 350);
  scheduleJobEstimate();
}

function restoreLocalizationDraft() {
  let draft;
  try { draft = JSON.parse(localStorage.getItem(localizationDraftKey()) || "null"); }
  catch { draft = null; }
  if (!draft?.values) return false;
  for (const [id, value] of Object.entries(draft.values)) {
    const control = document.getElementById(id);
    if (!control) continue;
    if (["checkbox", "radio"].includes(control.type)) control.checked = Boolean(value);
    else if (!control.options || Array.from(control.options).some(option => option.value === value)) control.value = value;
    control.dispatchEvent(new Event("change"));
  }
  showLocalizationStep(draft.step || 1);
  $("#draft-status").textContent = "Đã khôi phục bản nháp";
  return true;
}

for (const control of localizationDraftControls()) {
  control.addEventListener("input", scheduleDraftSave);
  control.addEventListener("change", scheduleDraftSave);
}

function formatEstimateDuration(seconds) {
  if (seconds < 60) return `${seconds} giây`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return hours ? `${hours} giờ ${minutes ? `${minutes} phút` : ""}`.trim() : `${minutes} phút`;
}

async function updateJobEstimate() {
  const count = Math.max(1, localizationUrls().length);
  try {
    const estimate = await api("/api/jobs/estimate", {
      method: "POST",
      body: JSON.stringify({
        url_count: count,
        review_before_render: $("#review-mode-checkbox").checked,
        animated_subtitles: $("#animated-subtitle-checkbox").checked,
        max_concurrent: parseInt($("#max-concurrent-jobs").value, 10) || 2,
        processing_mode: $("#processing-mode-select").value,
        tts_provider: $("#tts-provider-select").value,
        contextual_translation: $("#translation-mode-select").value !== "faithful",
      }),
    });
    $("#estimate-credits").textContent = `${estimate.total_credits} credit`;
    $("#estimate-time").textContent =
      `${formatEstimateDuration(estimate.estimated_seconds_min)} – ${formatEstimateDuration(estimate.estimated_seconds_max)}`;
    $("#estimate-note").textContent = estimate.sample_size
      ? `Dựa trên ${estimate.sample_size} job gần đây.${estimate.excludes_manual_review_wait ? " Chưa gồm thời gian bạn sửa phụ đề." : ""}`
      : `Ước tính ban đầu.${estimate.excludes_manual_review_wait ? " Chưa gồm thời gian bạn sửa phụ đề." : ""}`;
  } catch {
    const credits = (bootstrapConfig.job_cost_credits || 0) * count;
    $("#estimate-credits").textContent = `${credits} credit`;
    $("#estimate-time").textContent = "Chưa xác định";
  }
}

function scheduleJobEstimate() {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(updateJobEstimate, 300);
}

function renderPreflightReport(report) {
  const status = $("#preflight-status");
  const list = $("#preflight-list");
  const issues = report?.issues || [];
  status.textContent = report?.ok
    ? "Sẵn sàng tạo job."
    : "Cần xử lý lỗi bên dưới trước khi tạo job.";
  list.innerHTML = issues.length
    ? issues.map(issue => `<li class="${escapeHtml(issue.severity || "warning")}">${escapeHtml(issue.message || "")}</li>`).join("")
    : `<li class="ok">Không phát hiện lỗi cấu hình cơ bản.</li>`;
}

async function runJobPreflight(urls, commonBody) {
  const report = await api("/api/jobs/preflight", {
    method: "POST",
    body: JSON.stringify({ url: urls[0] || "", url_count: urls.length, ...commonBody }),
  });
  renderPreflightReport(report);
  return report;
}

const PLATFORM_PRESETS = {
  tiktok: { aspect: "9:16", template: "social", effect: "karaoke", fontSize: 34, fontColor: "#ffe600", bg: "#00000080", label: "TikTok" },
  youtube: { aspect: "16:9", template: "professional", effect: "karaoke", fontSize: 28, fontColor: "#ffffff", bg: "#000000b8", label: "YouTube 16:9" },
  shorts: { aspect: "9:16", template: "professional", effect: "glow", fontSize: 32, fontColor: "#ffffff", bg: "#00000088", label: "YouTube Shorts" },
  reels: { aspect: "9:16", template: "vibrant", effect: "bounce", fontSize: 30, fontColor: "#ffffff", bg: "#00000070", label: "Instagram Reels" },
};

function applyPlatformPreset(name) {
  const preset = PLATFORM_PRESETS[name];
  if (!preset) return;
  $("#output-aspect-ratio").value = preset.aspect;
  $("#video-template-checkbox").checked = true;
  $("#video-template-panel").classList.remove("hidden");
  $("#video-template-select").value = preset.template;
  $("#template-video-quality").value = "high";
  $("#animated-subtitle-checkbox").checked = true;
  $("#animated-subtitle-panel").classList.remove("hidden");
  $("#subtitle-effect-select").value = preset.effect;
  $("#subtitle-font-size").value = preset.fontSize;
  $("#subtitle-font-color").value = preset.fontColor;
  $("#subtitle-bg-color").value = preset.bg;
  document.querySelectorAll("[data-platform-preset]").forEach(card => {
    card.classList.toggle("active", card.dataset.platformPreset === name);
  });
  $("#platform-preset-status").textContent = `Đã áp dụng ${preset.label}`;
  scheduleDraftSave();
  updateLocalizationSummary();
}

document.querySelectorAll("[data-platform-preset]").forEach(card => {
  card.addEventListener("click", () => applyPlatformPreset(card.dataset.platformPreset));
});

const SUBTITLE_TEMPLATES = {
  minimal: { effect: "fade", size: "28", font: "#ffffff", bg: "#000000b8", glow: "#ffffff" },
  karaoke: { effect: "karaoke", size: "34", font: "#ffe600", bg: "#00000080", glow: "#7c3aed" },
  glow: { effect: "glow", size: "32", font: "#ffffff", bg: "#00000040", glow: "#00e5ff" },
};

function applyRecommendedLocalizationDefaults() {
  $("#output-aspect-ratio").value = "auto";
  $("#video-template-checkbox").checked = true;
  $("#video-template-panel").classList.remove("hidden");
  $("#video-template-select").value = "social";
  $("#template-video-quality").value = "high";
  $("#animated-subtitle-checkbox").checked = true;
  $("#animated-subtitle-panel").classList.remove("hidden");
  $("#subtitle-effect-select").value = "karaoke";
  $("#subtitle-font-size").value = "34";
  $("#subtitle-font-color").value = "#ffe600";
  $("#subtitle-bg-color").value = "#00000080";
  $("#subtitle-glow-color").value = "#7c3aed";
  document.querySelectorAll("[data-platform-preset]").forEach(card => {
    card.classList.toggle("active", card.dataset.platformPreset === "tiktok");
  });
  document.querySelectorAll("[data-subtitle-template]").forEach(card => {
    card.classList.toggle("active", card.dataset.subtitleTemplate === "karaoke");
  });
  $("#platform-preset-status").textContent = "Mặc định: TikTok";
  $("#subtitle-template-status").textContent = "Mặc định: Karaoke";
  updateLocalizationSummary();
}

function applySubtitleTemplate(name) {
  const preset = SUBTITLE_TEMPLATES[name];
  if (!preset) return;
  $("#animated-subtitle-checkbox").checked = true;
  $("#animated-subtitle-panel").classList.remove("hidden");
  $("#subtitle-effect-select").value = preset.effect;
  $("#subtitle-font-size").value = preset.size;
  $("#subtitle-font-color").value = preset.font;
  $("#subtitle-bg-color").value = preset.bg;
  $("#subtitle-glow-color").value = preset.glow;
  document.querySelectorAll("[data-subtitle-template]").forEach(card => {
    card.classList.toggle("active", card.dataset.subtitleTemplate === name);
  });
  $("#subtitle-template-status").textContent = `Đã chọn ${name[0].toUpperCase() + name.slice(1)}`;
  scheduleDraftSave();
  updateLocalizationSummary();
}

document.querySelectorAll("[data-subtitle-template]").forEach(card => {
  card.addEventListener("click", () => applySubtitleTemplate(card.dataset.subtitleTemplate));
});

$("#submit-btn").onclick = async () => {
  $("#submit-error").textContent = "";
  const urls = localizationUrls();
  if (!validateLocalizationStep(1)) {
    showLocalizationStep(1);
    return;
  }
  const brandingConfig = getBrandingConfig();
  if (brandingConfig.enabled && !brandingConfig.text) {
    $("#submit-error").textContent = "Đã bật bảo vệ thương hiệu nhưng chưa nhập tên kênh.";
    return;
  }
  const logoEnabled = $("#logo-enable-checkbox").checked;
  if (logoEnabled && !uploadedLogoId) {
    $("#submit-error").textContent = "Chọn ảnh logo trước (hoặc bỏ tick 'Chèn logo')";
    return;
  }
  const reviewMode = $("#review-mode-checkbox").checked;
  if (channelModeEnabled() && reviewMode) {
    $("#submit-error").textContent = "Xử lý toàn bộ kênh không hỗ trợ dừng từng video để sửa phụ đề thủ công.";
    return;
  }
  if (reviewMode && urls.length > 1) {
    $("#submit-error").textContent = "Chế độ 'xem & sửa phụ đề' chỉ dùng được với 1 link mỗi lần, không dùng chung với xử lý hàng loạt";
    return;
  }
  const activeCorner = document.querySelector(".corner-opt.active");
  const commonBody = {
    target_language: $("#lang-select").value,
    source_language: $("#source-lang-select").value,
    tts_voice: $("#voice-select").value || null,
    tts_provider: $("#tts-provider-select").value,
    tts_style: $("#tts-style-select").value,
    tts_model: $("#tts-model-select").value || null,
    processing_mode: $("#processing-mode-select").value,
    translation_mode: $("#translation-mode-select").value,
    translation_model: $("#translation-model-select").value || null,
    translation_tone: $("#translation-tone-select").value,
    translation_audience: $("#translation-audience-input").value || null,
    translation_glossary: $("#translation-glossary-input").value || null,
    remix_enabled: $("#remix-enable-checkbox").checked,
    remix_platforms: $("#remix-enable-checkbox").checked ? getRemixPlatforms() : [],
    remix_goal: $("#remix-goal-select").value,
    remix_strength: $("#remix-strength-select").value,
    subtitle_offset_seconds: parseFloat($("#subtitle-offset-input").value) || 0,
    logo_path: logoEnabled ? uploadedLogoId : null,
    logo_corner: activeCorner ? activeCorner.dataset.corner : "bottom_right",
    logo_size_px: parseInt($("#logo-size-input").value, 10) || 120,
    branding_config: getBrandingConfig(),
    publishing_config: getPublishingPackConfig(),
    review_before_render: reviewMode,
    animated_subtitle_config: getAnimatedSubtitleConfig(),
    priority: $("#queue-priority-checkbox").checked ? "high" : "normal",
    max_concurrent: parseInt($("#max-concurrent-jobs").value, 10) || 2,
    video_template_config: getVideoTemplateConfig(),
    transform_config: getTransformConfig(),
  };

  $("#submit-btn").disabled = true;
  const failures = [];
  try {
    if (channelModeEnabled()) {
      $("#preflight-status").textContent = "Đang quét kênh và tạo job cho từng video...";
      $("#preflight-list").innerHTML = "";
      const result = await api("/api/channel-downloads/process", {
        method: "POST",
        body: JSON.stringify({
          url: urls[0],
          max_videos: parseInt($("#channel-max-videos").value, 10) || 0,
          skip_existing: $("#channel-skip-existing").checked,
          deep_scan: true,
          ...commonBody,
        }),
      });
      const notes = [
        `Catalog ${result.catalog_total ?? result.scanned} video`,
        result.newly_discovered ? `mới phát hiện ${result.newly_discovered}` : "",
        result.scan_complete ? "đã quét hết" : "catalog chưa hoàn tất",
        `tạo ${result.created_count} job`,
        `bỏ qua ${result.skipped_existing_count} video cũ`,
        result.failed_count ? `${result.failed_count} lỗi` : "",
      ].filter(Boolean);
      $("#preflight-status").textContent = notes.join(" · ");
      $("#preflight-list").innerHTML = (result.warnings || []).map(
        warning => `<li class="warning">${escapeHtml(warning)}</li>`
      ).join("");
      if (result.failed_count) {
        $("#submit-error").textContent = result.failed.slice(0, 5)
          .map(item => `${item.source_url}: ${item.error}`).join(" | ");
      } else {
        $("#url-input").value = "";
        $("#channel-analysis-status").textContent = "Đã tạo batch xử lý kênh.";
        localStorage.removeItem(localizationDraftKey());
        $("#draft-status").textContent = "Đã xóa bản nháp sau khi gửi";
        showLocalizationStep(1);
      }
    } else {
      $("#preflight-status").textContent = "Đang kiểm tra trước khi tạo job...";
      $("#preflight-list").innerHTML = "";
      const preflight = await runJobPreflight(urls, commonBody);
      if (!preflight.ok) {
        $("#submit-error").textContent = "Preflight check chưa đạt. Sửa các lỗi được liệt kê rồi chạy lại.";
        return;
      }
      for (const url of urls) {
        try {
          await api("/api/jobs", { method: "POST", body: JSON.stringify({ url, ...commonBody }) });
        } catch (e) {
          failures.push(`${url}: ${e.message}`);
        }
      }
      if (failures.length) {
        $("#submit-error").textContent = `${failures.length}/${urls.length} link gửi thất bại — ${failures.join(" | ")}`;
      } else {
        $("#url-input").value = "";
        localStorage.removeItem(localizationDraftKey());
        $("#draft-status").textContent = "Đã xóa bản nháp sau khi gửi";
        showLocalizationStep(1);
      }
    }
    refreshJobs();
    refreshMe();
  } finally { $("#submit-btn").disabled = false; }
};

let latestAffiliateReview = null;
let affiliateProductMedia = [];

$("#affiliate-advanced-checkbox").onchange = (ev) => {
  $("#affiliate-advanced-panel").classList.toggle("hidden", !ev.target.checked);
};

function renderAffiliateProductMediaList() {
  const box = $("#affiliate-product-media-list");
  if (!affiliateProductMedia.length) {
    box.textContent = "Chưa upload product media. Video sẽ dùng stock footage nếu có cấu hình Pexels/Pixabay.";
    return;
  }
  box.innerHTML = affiliateProductMedia.map((item, idx) =>
    `${idx + 1}. ${escapeHtml(item.filename)} (${escapeHtml(item.media_type)})`
  ).join("<br>");
}
renderAffiliateProductMediaList();

$("#affiliate-product-media-input").onchange = async (ev) => {
  const files = Array.from(ev.target.files || []);
  if (!files.length) return;
  $("#affiliate-error").style.color = "var(--text-dim)";
  $("#affiliate-error").textContent = "Đang upload product media...";
  try {
    for (const file of files.slice(0, 12 - affiliateProductMedia.length)) {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/product-media/upload", { method: "POST", body: form });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed: HTTP ${resp.status}`);
      }
      affiliateProductMedia.push(await resp.json());
    }
    renderAffiliateProductMediaList();
    $("#affiliate-error").style.color = "var(--ok)";
    $("#affiliate-error").textContent = "Đã upload product media. Hero/reveal sẽ dùng media thật; cảnh người mẫu sẽ ưu tiên Stock/AI context.";
  } catch (e) {
    $("#affiliate-error").style.color = "var(--err)";
    $("#affiliate-error").textContent = e.message;
  } finally {
    ev.target.value = "";
  }
};

function affiliatePayload() {
  return {
    product_url: $("#affiliate-product-url").value.trim() || null,
    product_name: $("#affiliate-product-name").value.trim(),
    product_claims: $("#affiliate-claims").value.trim(),
    pros: $("#affiliate-pros").value.trim(),
    cons: $("#affiliate-cons").value.trim(),
    audience: $("#affiliate-audience").value.trim(),
    real_experience: $("#affiliate-experience").value.trim(),
    model_prompt: $("#affiliate-model-prompt").value.trim(),
    target_language: ($("#creator-lang-select") && $("#creator-lang-select").value) || $("#lang-select").value || "vi",
    duration_seconds: parseInt($("#affiliate-duration").value, 10) || 30,
    platform: $("#affiliate-platform").value,
    creative_format: $("#affiliate-creative-format").value,
  };
}

function showAffiliateReview(data) {
  latestAffiliateReview = data;
  $("#affiliate-output").classList.remove("hidden");
  $("#affiliate-narration-output").value = data.narration_script || "";
  $("#affiliate-broll-output").value = data.broll_plan || "";
  $("#affiliate-title-output").value = data.title || "";
  $("#affiliate-caption-output").value = data.caption || "";
  $("#affiliate-hashtags-output").value = (data.hashtags || []).join(", ");
  const warnings = data.compliance_warnings || [];
  const qualityNotes = data.quality_notes || [];
  const qualityHtml = qualityNotes.length
    ? `<br><strong>Quality upgrade:</strong><br>${qualityNotes.map(w => `- ${escapeHtml(w)}`).join("<br>")}`
    : "";
  $("#affiliate-warnings-output").innerHTML = warnings.length
    ? `<strong>Compliance check:</strong><br>${warnings.map(w => `- ${escapeHtml(w)}`).join("<br>")}${qualityHtml}`
    : `<span style="color:var(--ok)">Compliance check: OK. Ghi chú link sản phẩm đã được chèn vào caption.</span>${qualityHtml}`;
  $("#affiliate-send-creator-btn").disabled = false;
  $("#affiliate-copy-caption-btn").disabled = false;
}

$("#affiliate-generate-btn").onclick = async () => {
  $("#affiliate-error").style.color = "var(--text-dim)";
  $("#affiliate-error").textContent = "";
  const button = $("#affiliate-generate-btn");
  button.disabled = true;
  button.classList.add("is-loading");
  button.innerHTML = `<span class="loading-spinner" aria-hidden="true"></span><span>Đang gen...</span>`;
  try {
    const payload = affiliatePayload();
    if (!payload.product_name) throw new Error("Nhập tên sản phẩm");
    if (!payload.real_experience) throw new Error("Nhập trải nghiệm thật / ghi chú bắt buộc");
    const data = await api("/api/affiliate/review", { method: "POST", body: JSON.stringify(payload) });
    showAffiliateReview(data);
    $("#affiliate-error").style.color = "var(--ok)";
    $("#affiliate-error").textContent = affiliateProductMedia.length
      ? "Đã tạo product ad creative. Hero/reveal dùng product media; cảnh người mẫu dùng Stock/AI context nếu có."
      : "Đã tạo product ad creative. Nên upload ảnh/video sản phẩm thật trước khi tạo video để tránh cảnh stock/AI bị giả.";
  } catch (e) {
    $("#affiliate-error").style.color = "var(--err)";
    $("#affiliate-error").textContent = e.message;
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.textContent = "Gen product ad";
  }
};

$("#affiliate-send-creator-btn").onclick = () => {
  if (!latestAffiliateReview) return;
  const product = $("#affiliate-product-name").value.trim();
  $("#creator-topic").value = `Product ad: ${product}`;
  $("#creator-duration").value = String(latestAffiliateReview.duration_seconds || $("#affiliate-duration").value || 30);
  $("#creator-image-provider").value = "stock";
  $("#creator-keywords").value = $("#affiliate-hashtags-output").value.replace(/#/g, "");
  $("#creator-script").value = $("#affiliate-broll-output").value;
  $("#creator-narration").value = $("#affiliate-narration-output").value;
  $("#script-style-select").value = "review";
  $("#script-tone-select").value = "casual";
  window._creatorSuggestionCache = null;
  window._creatorSuggestionPending = null;
  updateCreatorSubmitState();
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = affiliateProductMedia.length
    ? "Affiliate media đã sẵn sàng: hero/reveal dùng product ad mockup; cảnh người mẫu/problem/objection ưu tiên Stock/AI context."
    : "Affiliate flow đang để visual source là Stock. Upload product media để cảnh review khớp sản phẩm hơn.";
  $("#creator-topic").scrollIntoView({ behavior: "smooth", block: "center" });
};

$("#affiliate-copy-caption-btn").onclick = async () => {
  const text = `${$("#affiliate-caption-output").value.trim()}\n${$("#affiliate-hashtags-output").value.trim()}`;
  if (!text.trim()) return;
  try {
    await navigator.clipboard.writeText(text);
    $("#affiliate-error").style.color = "var(--ok)";
    $("#affiliate-error").textContent = "Đã copy caption.";
  } catch {
    $("#affiliate-caption-output").select();
    $("#affiliate-error").style.color = "var(--warn)";
    $("#affiliate-error").textContent = "Trình duyệt không cho copy tự động, caption đã được bôi chọn.";
  }
};

async function loadCreatorSuggestions() {
  const topic = $("#creator-topic").value.trim();
  if (!topic) throw new Error("Nhập chủ đề video trước");
  const requestBody = {
    topic,
    target_language: $("#creator-lang-select").value || "vi",
    aspect_ratio: $("#creator-aspect").value,
    duration_seconds: parseInt($("#creator-duration").value, 10),
    transition: $("#creator-transition").value,
    advanced_options: getAdvancedScriptOptions(),
  };
  const cacheKey = JSON.stringify(requestBody);
  if (window._creatorSuggestionCache && window._creatorSuggestionCache.key === cacheKey) {
    return window._creatorSuggestionCache.data;
  }
  // Reuse an in-flight request too: clicking Keyword, Visual and Voice in
  // quick succession must generate once, not spend three API quota units.
  if (window._creatorSuggestionPending && window._creatorSuggestionPending.key === cacheKey) {
    return window._creatorSuggestionPending.promise;
  }
  const promise = api("/api/creator/suggestions", { method: "POST", body: JSON.stringify(requestBody) })
    .then(data => {
      if (data.generator && data.generator !== "template") {
        window._creatorSuggestionCache = { key: cacheKey, data };
      }
      return data;
    })
    .finally(() => {
      if (window._creatorSuggestionPending && window._creatorSuggestionPending.key === cacheKey) {
        window._creatorSuggestionPending = null;
      }
    });
  window._creatorSuggestionPending = { key: cacheKey, promise };
  return promise;
}

function creatorGeneratorMessage(data, successText) {
  const timing = data.duration_seconds
    ? ` · ${data.duration_seconds}s · ${data.scene_count || 0} cảnh · ${data.narration_word_count || 0} từ voice`
    : "";
  const quality = (data.quality_notes || []).length ? ` · Hook/SEO/visual đã được tối ưu` : "";
  if (data.warning) return `${data.warning}${quality}${timing}`;
  const searched = (data.search_queries || []).slice(0, 2).join(" · ");
  const grounding = data.grounded ? " + Google Search" : "";
  return `${successText} bằng ${data.model || "AI"}${grounding}${data.cached ? " (cache)" : ""}${searched ? ` (${searched})` : ""}.${quality}${timing}`;
}

function ensureCreatorSuggestionCurrent(data) {
  const selectedDuration = parseInt($("#creator-duration").value, 10);
  if (data.duration_seconds && Number(data.duration_seconds) !== selectedDuration) {
    throw new Error("Thời lượng đã thay đổi trong lúc đang gen. Hãy gen lại nội dung mới.");
  }
  return data;
}

const creatorGenerateButtons = [
  $("#creator-keywords-generate"),
  $("#creator-script-generate"),
  $("#creator-narration-generate"),
];
creatorGenerateButtons.forEach(button => { button.dataset.idleLabel = button.textContent; });

function setCreatorGenerationLoading(activeButton, loading, loadingLabel = "Đang gen...") {
  creatorGenerateButtons.forEach(button => { button.disabled = loading; });
  if (loading) {
    activeButton.classList.add("is-loading");
    activeButton.innerHTML = `<span class="loading-spinner" aria-hidden="true"></span><span>${loadingLabel}</span>`;
    activeButton.setAttribute("aria-busy", "true");
  } else {
    creatorGenerateButtons.forEach(button => {
      button.classList.remove("is-loading");
      button.textContent = button.dataset.idleLabel;
      button.removeAttribute("aria-busy");
    });
  }
}

let activeCreatorJobId = null;
let creatorJobSubmitting = false;

function creatorFormComplete() {
  return Boolean(
    $("#creator-topic").value.trim() &&
    $("#creator-keywords").value.trim() &&
    $("#creator-script").value.trim() &&
    $("#creator-narration").value.trim()
  );
}

function updateCreatorSubmitState() {
  const button = $("#creator-submit-btn");
  if (creatorJobSubmitting || activeCreatorJobId) return;
  button.disabled = !creatorFormComplete();
  button.title = button.disabled
    ? "Cần đủ chủ đề, keyword, visual brief và kịch bản voice"
    : "Tạo video từ nội dung đã chuẩn bị";
}

function setCreatorSubmitLoading(loading, label = "Đang tạo video...") {
  const button = $("#creator-submit-btn");
  creatorJobSubmitting = loading;
  button.classList.toggle("is-loading", loading);
  button.disabled = loading || !creatorFormComplete();
  button.innerHTML = loading
    ? `<span class="loading-spinner" aria-hidden="true"></span><span>${label}</span>`
    : "Tạo video AI";
  if (loading) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

function jobProgress(job) {
  if (!job) return 0;
  if (job.status === "done") return 100;
  if (job.status === "error") return 0;
  if (job.status === "cancelled") return 0;
  if (job.status === "queued") return 2;
  const note = (job.progress_note || "").toLowerCase();
  const explicit = note.match(/^\[(\d{1,3})%\]/);
  if (explicit) return Math.max(0, Math.min(100, Number(explicit[1])));
  const scene = note.match(/(?:lấy|dựng) cảnh\s+(\d+)\/(\d+)/) || note.match(/khung hình\s+(\d+)\/(\d+)/);
  if (scene) return Math.min(65, 10 + Math.round((Number(scene[1]) / Math.max(1, Number(scene[2]))) * 55));
  if (note.includes("ghép") && note.includes("chuyển cảnh")) return 70;
  if (note.includes("giọng đọc")) return 80;
  if (note.includes("subtitle")) return 92;
  if (note.includes("dựng video")) return 5;
  if (note.includes("tải video")) return 5;
  if (note.includes("dịch phụ đề")) return 45;
  if (note.includes("dịch, lồng tiếng, render")) return 20;
  if (note.includes("lồng tiếng") && note.includes("render")) return 70;
  return job.status === "running" ? 5 : 0;
}

function jobProgressNote(job) {
  return String(job.progress_note || "").replace(/^\[\d{1,3}%\]\s*/, "");
}

function showCreatorProgress(job) {
  const percent = jobProgress(job);
  $("#creator-progress").classList.remove("hidden");
  $("#creator-progress-label").textContent = jobProgressNote(job) || "Đang xử lý video...";
  $("#creator-progress-percent").textContent = `${percent}%`;
  $("#creator-progress-fill").style.width = `${percent}%`;
  setCreatorSubmitLoading(true, `Đang tạo ${percent}%`);
}

["creator-topic", "creator-keywords", "creator-script", "creator-narration"].forEach(id => {
  $(`#${id}`).addEventListener("input", updateCreatorSubmitState);
});

$("#creator-duration").addEventListener("change", () => {
  window._creatorSuggestionCache = null;
  window._creatorSuggestionPending = null;
  $("#creator-script").value = "";
  $("#creator-narration").value = "";
  const seconds = parseInt($("#creator-duration").value, 10);
  const label = seconds >= 60 ? `${seconds / 60} phút` : `${seconds} giây`;
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = `Đã đổi thời lượng thành ${label}. Hãy gen lại visual brief và kịch bản voice.`;
  updateCreatorSubmitState();
});

$("#creator-keywords-generate").onclick = async () => {
  const button = $("#creator-keywords-generate");
  setCreatorGenerationLoading(button, true, "Đang gen keyword...");
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = "Đang gen keyword...";
  try {
    const data = ensureCreatorSuggestionCurrent(await loadCreatorSuggestions());
    $("#creator-keywords").value = data.keywords.join(", ");
    $("#creator-error").style.color = "var(--ok)";
    $("#creator-error").textContent = creatorGeneratorMessage(data, "Đã gen keyword");
  } catch (e) {
    $("#creator-error").style.color = "var(--err)";
    $("#creator-error").textContent = e.message;
  } finally {
    setCreatorGenerationLoading(button, false);
    updateCreatorSubmitState();
  }
};

$("#creator-keywords-copy").onclick = async () => {
  const text = $("#creator-keywords").value.trim();
  if (!text) { $("#creator-error").textContent = "Chưa có keyword để copy"; return; }
  try {
    await navigator.clipboard.writeText(text);
    $("#creator-error").style.color = "var(--ok)";
    $("#creator-error").textContent = "Đã copy keyword.";
  } catch {
    $("#creator-keywords").select();
    $("#creator-error").style.color = "var(--warn)";
    $("#creator-error").textContent = "Trình duyệt không cho copy tự động, keyword đã được bôi chọn.";
  }
};

$("#creator-script-generate").onclick = async () => {
  const button = $("#creator-script-generate");
  setCreatorGenerationLoading(button, true, "Đang gen visual brief...");
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = "Đang gen visual brief...";
  try {
    const data = ensureCreatorSuggestionCurrent(await loadCreatorSuggestions());
    $("#creator-script").value = data.visual_brief || data.script;
    if (!$("#creator-keywords").value.trim()) $("#creator-keywords").value = data.keywords.join(", ");
    $("#creator-error").style.color = "var(--ok)";
    $("#creator-error").textContent = creatorGeneratorMessage(data, "Đã gen visual brief");
  } catch (e) {
    $("#creator-error").style.color = "var(--err)";
    $("#creator-error").textContent = e.message;
  } finally {
    setCreatorGenerationLoading(button, false);
    updateCreatorSubmitState();
  }
};

$("#creator-narration-generate").onclick = async () => {
  const button = $("#creator-narration-generate");
  setCreatorGenerationLoading(button, true, "Đang gen voice...");
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = "Đang gen kịch bản voice...";
  try {
    const data = ensureCreatorSuggestionCurrent(await loadCreatorSuggestions());
    $("#creator-narration").value = data.narration_script;
    $("#creator-error").style.color = "var(--ok)";
    $("#creator-error").textContent = creatorGeneratorMessage(data, "Đã gen kịch bản voice");
  } catch (e) {
    $("#creator-error").style.color = "var(--err)";
    $("#creator-error").textContent = e.message;
  } finally {
    setCreatorGenerationLoading(button, false);
    updateCreatorSubmitState();
  }
};

$("#creator-topic").addEventListener("blur", async () => {
  if (!$("#creator-topic").value.trim() || $("#creator-keywords").value.trim()) return;
  try {
    const data = ensureCreatorSuggestionCurrent(await loadCreatorSuggestions());
    $("#creator-keywords").value = data.keywords.join(", ");
  } catch { /* non-critical convenience */ }
  finally { updateCreatorSubmitState(); }
});

$("#creator-submit-btn").onclick = async () => {
  $("#creator-error").style.color = "var(--text-dim)";
  $("#creator-error").textContent = "";
  const topic = $("#creator-topic").value.trim();
  const creatorBranding = getCreatorBrandingConfig();
  if (creatorBranding.enabled && !creatorBranding.text) {
    $("#creator-error").textContent = "Đã bật bảo vệ thương hiệu nhưng chưa nhập tên kênh.";
    return;
  }
  if (!creatorFormComplete()) {
    $("#creator-error").textContent = "Cần nhập đủ keyword, visual brief và kịch bản voice trước khi tạo video.";
    updateCreatorSubmitState();
    return;
  }
  setCreatorSubmitLoading(true, "Đang gửi job...");
  try {
    $("#creator-error").textContent = "Đang gửi job tạo video...";
    const job = await api("/api/creator/jobs", { method: "POST", body: JSON.stringify({
      topic,
      script: $("#creator-script").value.trim(),
      narration_script: $("#creator-narration").value.trim(),
      target_language: $("#creator-lang-select").value || "vi",
      aspect_ratio: $("#creator-aspect").value,
      duration_seconds: parseInt($("#creator-duration").value, 10) || 30,
      transition: $("#creator-transition").value,
      tts_voice: $("#creator-voice-select").value || null,
      image_provider: $("#creator-image-provider").value,
      product_media_paths: affiliateProductMedia.map(item => item.media_id),
      branding_config: getCreatorBrandingConfig(),
    })});
    activeCreatorJobId = job.id;
    showCreatorProgress(job);
    $("#creator-error").style.color = "var(--ok)";
    $("#creator-error").textContent = "Đã nhận job. Tiến độ sẽ được cập nhật tự động.";
    refreshJobs();
    refreshMe();
  } catch (e) {
    activeCreatorJobId = null;
    $("#creator-error").style.color = "var(--err)";
    $("#creator-error").textContent = e.message;
  } finally {
    if (!activeCreatorJobId) setCreatorSubmitLoading(false);
  }
};

// Trend Scanner functionality
$("#trend-scan-btn").onclick = async () => {
  let topic = $("#trend-topic").value.trim();
  // Clean up topic - remove common UI text that might be accidentally included
  topic = topic.replace(/VN\s*Bỏ qua điều hướng Tìm kiếm.*$/, '').trim();
  topic = topic.replace(/Tạo\s*Hình ảnh đại diện.*$/, '').trim();
  topic = topic.replace(/Official Audio Video.*$/, '').trim();

  if (!topic) {
    $("#trend-error").style.color = "var(--err)";
    $("#trend-error").textContent = "Vui lòng nhập chủ đề cần quét";
    return;
  }

  const platforms = Array.from(
    document.querySelectorAll('[data-feature="trend"] input[type="checkbox"][value]:checked')
  ).map(cb => cb.value);
  if (platforms.length === 0) {
    $("#trend-error").style.color = "var(--err)";
    $("#trend-error").textContent = "Vui lòng chọn ít nhất một platform";
    return;
  }

  const maxResults = parseInt($("#trend-max-results").value, 10) || 20;
  const useAgentReach = $("#trend-agent-reach-fallback").checked;

  $("#trend-progress").classList.remove("hidden");
  $("#trend-progress-label").textContent = "Đang quét...";
  $("#trend-progress-fill").style.width = "0%";
  $("#trend-error").textContent = "";
  $("#trend-warnings").style.display = "none";
  $("#trend-results").style.display = "none";
  $("#trend-scan-btn").disabled = true;

  try {
    const result = await api("/api/trends/scan", {
      method: "POST",
      body: JSON.stringify({
        topic,
        platforms,
        max_results: maxResults,
        use_agent_reach_fallback: useAgentReach,
      }),
    });

    $("#trend-progress-fill").style.width = "100%";
    $("#trend-progress-label").textContent = "Hoàn thành";

    if (result.warnings && result.warnings.length > 0) {
      $("#trend-warnings").textContent = result.warnings.join("; ");
      $("#trend-warnings").style.display = "block";
    }

    if (result.items && result.items.length > 0) {
      const itemsList = $("#trend-items-list");
      itemsList.innerHTML = result.items.map(item => `
        <div style="padding:8px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">
          <div style="flex:1">
            <div style="font-weight:600;font-size:13px">${escapeHtml(item.title || item.source_url)}</div>
            <div style="font-size:11px;color:var(--text-dim)">${item.platform} · ${item.author || 'Unknown'}</div>
            <div style="font-size:11px;color:var(--text-dim)">Score: ${item.trend_score?.toFixed(2) || 'N/A'}</div>
          </div>
          <button class="btn secondary small" data-url="${escapeHtml(item.source_url)}" data-platform="${escapeHtml(item.platform)}">Dùng</button>
        </div>
      `).join("");

      // Add click handlers for "Dùng" buttons
      itemsList.querySelectorAll("button").forEach(btn => {
        btn.onclick = () => {
          const url = btn.dataset.url;
          $("#url-input").value = url;
          $("#url-count-help").textContent = "Đã chọn 1 link";
          alert("Đã thêm link vào form dịch video");
        };
      });

      $("#trend-results").style.display = "block";
    } else {
      $("#trend-error").style.color = "var(--warn)";
      $("#trend-error").textContent = "Không tìm thấy kết quả nào";
    }
  } catch (e) {
    $("#trend-error").style.color = "var(--err)";
    $("#trend-error").textContent = e.message || "Lỗi khi quét trend";
  } finally {
    $("#trend-progress").classList.add("hidden");
    $("#trend-scan-btn").disabled = false;
  }
};

// Check Agent-Reach availability on load
(async () => {
  try {
    const providers = await api("/api/trends/providers");
    if (!providers.agent_reach_available) {
      $("#trend-agent-reach-warning").style.display = "block";
    }
  } catch (e) {
    console.warn("Failed to check trend providers:", e);
  }
})();

const STATUS_LABEL = { queued: "Đang chờ", running: "Đang xử lý", review: "Chờ sửa phụ đề", done: "Xong", error: "Lỗi", cancelled: "Đã dừng" };

function _dateToUnix(dateStr, endOfDay) {
  if (!dateStr) return null;
  const d = new Date(dateStr + (endOfDay ? "T23:59:59" : "T00:00:00"));
  return d.getTime() / 1000;
}

function updateStats(jobs) {
  const total = jobs.length;
  const completed = jobs.filter(j => j.status === "done").length;
  const running = jobs.filter(j => j.status === "running").length;
  const queued = jobs.filter(j => j.status === "queued").length;
  const error = jobs.filter(j => j.status === "error").length;
  const processing = running + queued;

  // Calculate total duration (in minutes)
  const totalDuration = jobs.reduce((sum, job) => {
    const duration = job.duration_seconds || 0;
    return sum + duration;
  }, 0) / 60;

  // Update stat boxes
  $("#stat-total").textContent = total;
  $("#stat-completed").textContent = completed;
  $("#stat-processing").textContent = processing;
  $("#stat-error").textContent = error;
  $("#stat-duration").textContent = Math.round(totalDuration);

  // Update progress bar
  if (total > 0) {
    const donePercent = (completed / total) * 100;
    const runningPercent = (running / total) * 100;
    const queuedPercent = (queued / total) * 100;
    const errorPercent = (error / total) * 100;

    $("#bar-done").style.flex = `${donePercent}`;
    $("#bar-running").style.flex = `${runningPercent}`;
    $("#bar-queued").style.flex = `${queuedPercent}`;
    $("#bar-error").style.flex = `${errorPercent}`;
  } else {
    $("#bar-done").style.flex = "0";
    $("#bar-running").style.flex = "0";
    $("#bar-queued").style.flex = "0";
    $("#bar-error").style.flex = "0";
  }

  // Update labels
  $("#label-done").textContent = completed;
  $("#label-running").textContent = running;
  $("#label-queued").textContent = queued;
  $("#label-error").textContent = error;
}

async function refreshJobs() {
  const q = $("#history-search").value.trim();
  const status = $("#history-status-filter").value;
  const dateFrom = _dateToUnix($("#history-date-from").value, false);
  const dateTo = _dateToUnix($("#history-date-to").value, true);
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();

  const jobs = await api("/api/jobs" + (qs ? `?${qs}` : ""));

  // Update statistics
  updateStats(jobs);
  refreshJobQueueStatus();
  if (!activeCreatorJobId) {
    const runningCreator = jobs.find(job =>
      String(job.source_url || "").startsWith("creator:") &&
      (job.status === "queued" || job.status === "running")
    );
    if (runningCreator) activeCreatorJobId = runningCreator.id;
  }
  if (activeCreatorJobId) {
    const activeCreatorJob = jobs.find(job => job.id === activeCreatorJobId);
    if (activeCreatorJob && (activeCreatorJob.status === "queued" || activeCreatorJob.status === "running")) {
      showCreatorProgress(activeCreatorJob);
    } else if (activeCreatorJob) {
      const succeeded = activeCreatorJob.status === "done";
      const percent = succeeded ? 100 : 0;
      $("#creator-progress").classList.remove("hidden");
      $("#creator-progress-label").textContent = succeeded ? "Hoàn tất video" : "Tạo video không thành công";
      $("#creator-progress-percent").textContent = `${percent}%`;
      $("#creator-progress-fill").style.width = `${percent}%`;
      activeCreatorJobId = null;
      setCreatorSubmitLoading(false);
      updateCreatorSubmitState();
    }
  }
  const latestDone = jobs.find(job => job.status === "done" && job.has_video);
  // Pick an initial video once. Polling must never replace a video the user
  // is currently watching merely because a newer job just completed.
  if (latestResultJobId === null && latestDone) {
    showHistoryVideo(latestDone);
  } else if (latestResultJobId === null && !latestDone) {
    $("#latest-result-card").classList.add("hidden");
  }
  window._jobsById = Object.fromEntries(jobs.map(j => [j.id, j]));
  for (const job of jobs) {
    const prevStatus = _lastKnownStatus[job.id];
    if (prevStatus && prevStatus !== job.status && (job.status === "done" || job.status === "error")) {
      _notifyJobStatusChange(job);
    }
    _lastKnownStatus[job.id] = job.status;
  }
  const list = $("#job-list");
  list.innerHTML = "";
  $("#empty-history").classList.toggle("hidden", jobs.length > 0);
  for (const job of jobs) {
    const el = document.createElement("div");
    el.className = "job";
    const created = new Date(job.created_at * 1000).toLocaleString(uiLocale());
    const hasSubtitles = job.segments && job.segments.length > 0;
    const hasSourceSubtitles = job.source_segments && job.source_segments.length > 0;
    el.innerHTML = `
      <input type="checkbox" class="history-job-check" data-job-check="${job.id}" style="width:auto" ${selectedHistoryJobs.has(job.id) ? "checked" : ""}>
      <div class="job-main">
        <div class="job-head">
          <div class="job-info">
            <div class="job-url ${job.has_video ? "playable" : ""}" ${job.has_video ? `data-play-video="${job.id}" title="Bấm để xem video này"` : ""}>${job.title || job.source_url}${job.qc_warnings && job.qc_warnings.length ? ` <span title="${_escapeHtml(job.qc_warnings.join(' | '))}" style="color:var(--warn);cursor:help">⚠ Cần kiểm tra</span>` : ""}</div>
            <div class="job-meta">${created} · ${job.target_language.toUpperCase()} · ${jobProgressNote(job)}${job.publishing_pack_status && job.publishing_pack_status !== "disabled" ? ` · Publishing: ${_escapeHtml(job.publishing_pack_status)}` : ""}</div>
            ${job.source_channel_url || job.source_channel_title || job.source_uploader ? `<div class="job-meta" style="margin-top:4px">Kênh nguồn: <b>${_escapeHtml(job.source_channel_title || job.source_uploader || "Không rõ")}</b>${job.source_channel_url ? ` · <a href="${_escapeHtml(job.source_channel_url)}" target="_blank" rel="noopener">Mở kênh</a>` : ""}</div>` : ""}
            ${job.source_url && /^https?:\/\//i.test(job.source_url) ? `<div class="job-meta" style="margin-top:3px">Video gốc: <a href="${_escapeHtml(job.source_url)}" target="_blank" rel="noopener">${_escapeHtml(job.source_url)}</a></div>` : ""}
            ${(job.status === "queued" || job.status === "running" || job.status === "review" || job.status === "done" || job.status === "cancelled") ? `
              <div class="job-progress">
                <div class="progress-track"><div class="progress-fill" style="width:${jobProgress(job)}%"></div></div>
                <div class="job-progress-label">${jobProgress(job)}%</div>
              </div>` : ""}
          </div>
          <span class="badge ${job.status}">${STATUS_LABEL[job.status] || job.status}</span>
        </div>
        <div class="job-actions">
          ${(job.status === "queued" || job.status === "running" || job.status === "review") ? `<button class="btn danger small" data-cancel="${job.id}">Dừng</button>` : ""}
          ${job.status === "review" ? `<button class="btn small" data-review="${job.id}">Chỉnh sửa phụ đề &amp; Render</button>` : ""}
          ${job.has_video ? `
            <button class="btn secondary small" data-preview="${job.id}">Xem trước</button>
            <button class="btn secondary small" data-download-zip="${job.id}">Tải ZIP</button>
            <button class="btn gradient small" data-improve="${job.id}">Improve</button>
            ${job.has_publishing_pack ? `<button class="btn gradient small" data-publishing-pack="${job.id}">AI Publishing Pack</button>` : ""}
            <button class="btn secondary small" data-publish="${job.id}">Đăng lên MXH</button>
          ` : ""}
          ${hasSubtitles ? `<a class="btn secondary small" href="/api/jobs/${job.id}/subtitles.srt" download>SRT dịch</a>` : ""}
          ${hasSourceSubtitles ? `<a class="btn secondary small" href="/api/jobs/${job.id}/source-subtitles.srt" download>SRT gốc</a>` : ""}
          ${hasSubtitles || hasSourceSubtitles ? `<button class="btn secondary small" data-subtitle-view="${job.id}">Xem phụ đề</button>` : ""}
          ${job.status === "error" && !job.is_content_os ? `<button class="btn secondary small" data-retry="${job.id}">Thử lại</button>` : ""}
        </div>
      </div>
      <button class="btn danger small job-delete" data-delete="${job.id}" title="Xoá khỏi lịch sử">Xoá</button>
    `;
    list.appendChild(el);
  }
  list.querySelectorAll("[data-preview]").forEach(btn => {
    btn.onclick = () => openPreview(btn.dataset.preview);
  });
  list.querySelectorAll("[data-subtitle-view]").forEach(btn => {
    btn.onclick = () => openSubtitleViewer(btn.dataset.subtitleView);
  });
  list.querySelectorAll("[data-download-zip]").forEach(btn => {
    btn.onclick = () => downloadJobsZip([btn.dataset.downloadZip]);
  });
  list.querySelectorAll("[data-improve]").forEach(btn => {
    btn.onclick = () => openQualityReview(btn.dataset.improve);
  });
  list.querySelectorAll("[data-publishing-pack]").forEach(btn => {
    btn.onclick = () => openPublishingPack(btn.dataset.publishingPack);
  });
  list.querySelectorAll("[data-play-video]").forEach(title => {
    title.onclick = () => showHistoryVideo(window._jobsById[title.dataset.playVideo]);
  });
  list.querySelectorAll("[data-job-check]").forEach(box => {
    box.onchange = () => {
      if (box.checked) selectedHistoryJobs.add(box.dataset.jobCheck);
      else selectedHistoryJobs.delete(box.dataset.jobCheck);
      updateHistorySelection(jobs);
    };
  });
  updateHistorySelection(jobs);
  list.querySelectorAll("[data-publish]").forEach(btn => {
    btn.onclick = () => openPublish(btn.dataset.publish);
  });
  list.querySelectorAll("[data-review]").forEach(btn => {
    btn.onclick = () => openReview(btn.dataset.review);
  });
  list.querySelectorAll("[data-cancel]").forEach(btn => {
    btn.onclick = async () => {
      const accepted = await showConfirmDialog(
        "Dừng xử lý video?",
        "Job sẽ được đánh dấu đã dừng. Nếu tiến trình nền đang ở bước không thể ngắt tức thì, hệ thống sẽ bỏ kết quả khi nó quay lại điểm kiểm tra.",
        "Dừng xử lý",
      );
      if (!accepted) return;
      btn.disabled = true;
      try {
        await api(`/api/jobs/${btn.dataset.cancel}/cancel`, { method: "POST" });
        await refreshJobs();
      } catch (e) {
        alert(e.message);
      } finally {
        btn.disabled = false;
      }
    };
  });
  list.querySelectorAll("[data-retry]").forEach(btn => {
    btn.onclick = async () => {
      btn.disabled = true;
      try { await api(`/api/jobs/${btn.dataset.retry}/retry`, { method: "POST" }); refreshJobs(); refreshMe(); }
      catch (e) { alert(e.message); }
      finally { btn.disabled = false; }
    };
  });
  list.querySelectorAll("[data-delete]").forEach(btn => {
    btn.onclick = async () => {
      const job = window._jobsById[btn.dataset.delete];
      const label = job ? (job.title || job.source_url) : "video này";
      const accepted = await showConfirmDialog(
        "Xóa video khỏi lịch sử?",
        `Bạn có chắc muốn xóa “${label}”? Video và dữ liệu liên quan sẽ không thể khôi phục.`,
        "Xóa video",
      );
      if (!accepted) return;
      try {
        await api(`/api/jobs/${btn.dataset.delete}`, { method: "DELETE" });
        if (latestResultJobId === btn.dataset.delete) {
          latestResultJobId = null;
          $("#latest-result-video").removeAttribute("src");
          $("#latest-result-video").load();
        }
        refreshJobs();
      }
      catch (e) { alert(e.message); }
    };
  });
}

function updateHistorySelection(jobs = []) {
  const visibleIds = jobs.map(j => j.id);
  const selectedVisible = visibleIds.filter(id => selectedHistoryJobs.has(id)).length;
  $("#history-selected-count").textContent = selectedHistoryJobs.size ? `${selectedHistoryJobs.size} mục đã chọn` : "";
  $("#history-bulk-delete").disabled = selectedHistoryJobs.size === 0;
  $("#history-bulk-download").disabled = selectedHistoryJobs.size === 0;
  $("#history-select-all").checked = visibleIds.length > 0 && selectedVisible === visibleIds.length;
  $("#history-select-all").indeterminate = selectedVisible > 0 && selectedVisible < visibleIds.length;
}

$("#history-select-all").onchange = () => {
  const ids = Object.keys(window._jobsById || {});
  ids.forEach(id => $("#history-select-all").checked ? selectedHistoryJobs.add(id) : selectedHistoryJobs.delete(id));
  refreshJobs();
};
$("#history-bulk-delete").onclick = async () => {
  const ids = [...selectedHistoryJobs];
  if (!ids.length) return;
  const accepted = await showConfirmDialog(
    `Xóa ${ids.length} video đã chọn?`,
    "Tất cả video và dữ liệu liên quan trong những mục đã chọn sẽ bị xóa vĩnh viễn. Thao tác này không thể hoàn tác.",
    `Xóa ${ids.length} video`,
  );
  if (!accepted) return;
  try {
    const result = await api("/api/jobs/bulk-delete", { method: "POST", body: JSON.stringify({ job_ids: ids }) });
    if (latestResultJobId && ids.includes(latestResultJobId)) {
      latestResultJobId = null;
      $("#latest-result-video").removeAttribute("src");
      $("#latest-result-video").load();
    }
    selectedHistoryJobs.clear();
    await refreshJobs();
  } catch (e) { alert(e.message); }
};
$("#history-bulk-download").onclick = async () => {
  const ids = [...selectedHistoryJobs];
  if (!ids.length) return;
  await downloadJobsZip(ids);
};

async function downloadJobsZip(ids) {
  try {
    const resp = await fetch("/api/jobs/bulk-download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids }),
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const body = await resp.json();
        detail = body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `localized_videos_${Date.now()}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (e) { alert(e.message); }
}

let _historySearchDebounce = null;
$("#history-search").addEventListener("input", () => {
  clearTimeout(_historySearchDebounce);
  _historySearchDebounce = setTimeout(refreshJobs, 300);
});
$("#history-status-filter").onchange = refreshJobs;
$("#history-date-from").onchange = refreshJobs;
$("#history-date-to").onchange = refreshJobs;
$("#history-clear-btn").onclick = () => {
  $("#history-search").value = "";
  $("#history-date-from").value = "";
  $("#history-date-to").value = "";
  $("#history-status-filter").value = "";
  refreshJobs();
};

// ---------------- subtitle review editor ----------------
let currentReviewJobId = null;

async function openSubtitleViewer(jobId) {
  const { segments, source_segments: sourceSegments, quality } = await api(`/api/jobs/${jobId}/segments`);
  const sourceByTime = new Map((sourceSegments || []).map(s => [`${s.start}:${s.end}`, s]));
  const rows = (segments && segments.length ? segments : sourceSegments || []).map((segment, index) => {
    const source = sourceByTime.get(`${segment.start}:${segment.end}`) || (sourceSegments || [])[index] || {};
    const translated = (segments || [])[index] || {};
    return `
      <div class="review-segment">
        <div class="seg-time">${_fmtTime(segment.start)} → ${_fmtTime(segment.end)}</div>
        ${source.text ? `<div style="font-size:12px;color:var(--text-dim);white-space:pre-wrap">${_escapeHtml(source.text)}</div>` : ""}
        ${translated.text ? `<div style="font-size:14px;margin-top:5px;white-space:pre-wrap">${_escapeHtml(translated.text)}</div>` : ""}
      </div>
    `;
  }).join("");
  const warnings = (quality?.warnings || []).join(" ");
  $("#subtitle-viewer-quality").textContent = quality
    ? `Quality score: ${quality.score}/100${warnings ? ` · ${warnings}` : ""}`
    : "";
  $("#subtitle-viewer-list").innerHTML = rows || `<div class="muted-help">Job này chưa có phụ đề.</div>`;
  $("#subtitle-viewer-modal").classList.remove("hidden");
}

$("#subtitle-viewer-close").onclick = () => {
  $("#subtitle-viewer-modal").classList.add("hidden");
};

async function openReview(jobId) {
  currentReviewJobId = jobId;
  $("#review-error").textContent = "";
  const sourceVideo = $("#review-source-video");
  sourceVideo.src = `/api/jobs/${jobId}/source-video`;
  const { segments, quality } = await api(`/api/jobs/${jobId}/segments`);
  if (quality) {
    const warnings = (quality.warnings || []).join(" ");
    $("#review-error").style.color = warnings ? "var(--warn)" : "var(--accent)";
    $("#review-error").textContent = `Quality score: ${quality.score}/100${warnings ? ` · ${warnings}` : ""}`;
  }
  $("#review-segments-list").innerHTML = segments.map((s, i) => `
    <div class="review-segment" data-segment-row="${i}" data-start="${s.start}" data-end="${s.end}">
      <div class="seg-time">${_fmtTime(s.start)} → ${_fmtTime(s.end)}</div>
      <textarea data-idx="${i}" data-start="${s.start}" data-end="${s.end}">${_escapeHtml(s.text)}</textarea>
    </div>
  `).join("");
  $("#review-segments-list").querySelectorAll("[data-segment-row]").forEach(row => {
    row.onclick = ev => {
      if (ev.target.tagName === "TEXTAREA") return;
      sourceVideo.currentTime = Number(row.dataset.start);
      sourceVideo.play().catch(() => {});
    };
  });
  sourceVideo.ontimeupdate = () => {
    const current = sourceVideo.currentTime;
    $("#review-segments-list").querySelectorAll("[data-segment-row]").forEach(row => {
      row.classList.toggle("active", current >= Number(row.dataset.start) && current < Number(row.dataset.end));
    });
  };
  $("#review-modal").classList.remove("hidden");
}

function _fmtTime(seconds) {
  const totalTenths = Math.max(0, Math.round(Number(seconds || 0) * 10));
  const m = Math.floor(totalTenths / 600);
  const s = Math.floor((totalTenths % 600) / 10);
  const tenth = totalTenths % 10;
  return `${m}:${String(s).padStart(2, "0")}${tenth ? `.${tenth}` : ""}`;
}

function _collectReviewSegments() {
  return Array.from($("#review-segments-list").querySelectorAll("textarea")).map(ta => ({
    start: parseFloat(ta.dataset.start), end: parseFloat(ta.dataset.end), text: ta.value,
  }));
}

$("#review-close").onclick = () => {
  $("#review-modal").classList.add("hidden");
  $("#review-source-video").pause();
  $("#review-source-video").removeAttribute("src");
};

$("#review-save-btn").onclick = async () => {
  $("#review-error").textContent = "";
  try {
    await api(`/api/jobs/${currentReviewJobId}/segments`, {
      method: "PUT", body: JSON.stringify({ segments: _collectReviewSegments() }),
    });
    $("#review-error").style.color = "var(--accent)";
    $("#review-error").textContent = "Đã lưu.";
  } catch (e) { $("#review-error").style.color = "var(--err)"; $("#review-error").textContent = e.message; }
};

$("#review-render-btn").onclick = async () => {
  $("#review-error").textContent = "";
  try {
    await api(`/api/jobs/${currentReviewJobId}/segments`, {
      method: "PUT", body: JSON.stringify({ segments: _collectReviewSegments() }),
    });
    await api(`/api/jobs/${currentReviewJobId}/render`, { method: "POST" });
    $("#review-modal").classList.add("hidden");
    $("#review-source-video").pause();
    $("#review-source-video").removeAttribute("src");
    refreshJobs();
  } catch (e) { $("#review-error").textContent = e.message; }
};

// ---------------- feedback ----------------
$("#feedback-fab").onclick = () => {
  $("#feedback-status").textContent = "";
  $("#feedback-message").value = "";
  $("#feedback-modal").classList.remove("hidden");
};
$("#feedback-header-btn").onclick = () => $("#feedback-fab").click();
$("#feedback-close").onclick = () => $("#feedback-modal").classList.add("hidden");
$("#feedback-submit-btn").onclick = async () => {
  const message = $("#feedback-message").value.trim();
  if (!message) { $("#feedback-status").textContent = "Nhập nội dung góp ý trước đã."; return; }
  try {
    await api("/api/feedback", { method: "POST", body: JSON.stringify({ message, page: location.pathname }) });
    $("#feedback-status").textContent = "Cảm ơn bạn! Đã gửi góp ý.";
    setTimeout(() => $("#feedback-modal").classList.add("hidden"), 1200);
  } catch (e) { $("#feedback-status").textContent = e.message; }
};

// ---------------- AI Publishing Pack ----------------
function _copyText(value) {
  if (!value) return;
  navigator.clipboard?.writeText(value).catch(() => {});
}

const _publishingComponentLabels = {
  analysis: "Phân tích nội dung",
  youtube_metadata: "YouTube SEO",
  facebook_metadata: "Facebook SEO",
  youtube_thumbnails: "Thumbnail YouTube",
  facebook_thumbnails: "Thumbnail Facebook",
  publish_ready: "Publish-ready video",
};

function _publishingStatusLabel(status) {
  const labels = {
    pending: "⏳ Chờ",
    running: "⏳ Đang chạy",
    success: "✅ Hoàn tất",
    failed: "❌ Lỗi",
    skipped: "– Không bật",
  };
  return labels[status] || status || "Chưa có trạng thái";
}

function _publishingComponentGrid(jobId, components) {
  const entries = Object.entries(_publishingComponentLabels);
  return `<div class="stat-box" style="text-align:left">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <strong>Trạng thái từng thành phần</strong>
      ${entries.some(([key]) => (components[key] || {}).status === "failed")
        ? `<button class="btn gradient small" data-pack-retry="failed" data-job-id="${jobId}">🔄 Thử lại tất cả phần lỗi</button>`
        : `<span class="muted-help">Không có thành phần lỗi cần chạy lại.</span>`}
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;margin-top:10px">
      ${entries.map(([key, label]) => {
        const item = components[key] || { status: "pending", attempts: 0 };
        const failed = item.status === "failed";
        const error = item.error ? `<div class="muted-help" style="color:var(--err);margin-top:4px">${_escapeHtml(item.error)}</div>` : "";
        return `<div style="padding:10px;border:1px solid var(--border);border-radius:10px;background:var(--panel-2)">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
            <div><b>${_escapeHtml(label)}</b><div class="muted-help">${_publishingStatusLabel(item.status)} · ${Number(item.attempts || 0)} lần</div></div>
            ${failed ? `<button class="btn secondary small" data-pack-retry="${key}" data-job-id="${jobId}">Tạo lại</button>` : ""}
          </div>${error}
        </div>`;
      }).join("")}
    </div>
  </div>`;
}

function _publishingManualRegenerate(jobId, components) {
  return `<details class="stat-box" style="text-align:left">
    <summary style="cursor:pointer"><strong>Tạo lại thủ công</strong> <span class="muted-help">— chỉ chạy phần bạn chọn, không render lại video</span></summary>
    <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:10px">
      ${Object.entries(_publishingComponentLabels).map(([key, label]) => {
        const item = components[key] || {};
        if (item.status === "skipped") return "";
        return `<button class="btn secondary small" data-pack-retry="${key}" data-job-id="${jobId}">↻ ${_escapeHtml(label)}</button>`;
      }).join("")}
      <button class="btn secondary small" data-pack-retry="all" data-job-id="${jobId}">↻ Tạo lại toàn bộ Pack</button>
    </div>
  </details>`;
}

function _publishingThumbnailGrid(items) {
  if (!items || !items.length) return `<div class="muted-help">Chưa có thumbnail.</div>`;
  return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px">` +
    items.map((url, idx) => `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="Thumbnail ${idx + 1}" style="width:100%;border-radius:10px;border:1px solid var(--border)"></a>`).join("") +
    `</div>`;
}

async function retryPublishingComponent(jobId, component, button = null) {
  const original = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "Đang tạo lại...";
  }
  try {
    await api(`/api/jobs/${jobId}/publishing-pack/retry`, {
      method: "POST",
      body: JSON.stringify({ component }),
    });
    await openPublishingPack(jobId);
    if (typeof loadJobs === "function") loadJobs().catch(() => {});
  } catch (e) {
    const status = $("#publishing-pack-status");
    if (status) status.textContent = `Tạo lại thất bại: ${e.message}`;
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

function _wirePublishingRetryButtons(jobId, root) {
  root.querySelectorAll("[data-pack-retry]").forEach(btn => {
    btn.onclick = () => retryPublishingComponent(jobId, btn.dataset.packRetry, btn);
  });
}

async function openPublishingPack(jobId) {
  const modal = $("#publishing-pack-modal");
  const content = $("#publishing-pack-content");
  $("#publishing-pack-status").textContent = "Đang tải gói đăng bài...";
  content.innerHTML = "";
  modal.classList.remove("hidden");
  try {
    const pack = await api(`/api/jobs/${jobId}/publishing-pack`);
    window._publishingPackByJob = window._publishingPackByJob || {};
    window._publishingPackByJob[jobId] = pack;
    const source = pack.source_metadata || {};
    const analysis = pack.content_analysis || {};
    const yt = pack.youtube || {};
    const fb = pack.facebook || {};
    const originality = pack.originality_report || {};
    const components = pack.components || {};
    const overall = pack.overall_status || pack.status || "pending";
    const channelFit = analysis.channel_fit === "review_before_publish"
      ? "⚠ Nội dung có vẻ ngoài ngách kênh — cần duyệt trước khi đăng"
      : "✓ Nội dung khớp profile kênh";
    const overallLabel = {
      success: "✅ Publishing Pack hoàn tất",
      partial: "⚠ Publishing Pack hoàn thành một phần",
      failed: "❌ Publishing Pack có lỗi",
      running: "⏳ Publishing Pack đang chạy",
      pending: "⏳ Publishing Pack đang chờ",
    }[overall] || overall;
    $("#publishing-pack-status").textContent = `${overallLabel} · Tên truyện: ${analysis.story_name || "Chưa xác định"} · confidence ${Math.round((analysis.story_name_confidence || 0) * 100)}% · originality score ${originality.score ?? "?"}`;
    content.innerHTML = `
      ${_publishingComponentGrid(jobId, components)}
      <div class="stat-box" style="text-align:left">
        <strong>Metadata nguồn đã lấy</strong>
        <div style="margin-top:7px">Tiêu đề gốc: <b>${_escapeHtml(source.title || "Không có")}</b></div>
        <div class="muted-help">Tác giả: ${_escapeHtml(source.uploader || "Không rõ")} · Nền tảng: ${_escapeHtml(source.platform || "Không rõ")}</div>
        ${source.description ? `<details style="margin-top:7px"><summary>Mô tả gốc</summary><div class="muted-help" style="white-space:pre-wrap">${_escapeHtml(source.description)}</div></details>` : ""}
        ${source.source_thumbnail_url ? `<div style="margin-top:9px"><a href="${source.source_thumbnail_url}" target="_blank" rel="noopener"><img src="${source.source_thumbnail_url}" alt="Thumbnail nguồn" style="max-width:320px;width:100%;border-radius:10px;border:1px solid var(--border)"></a></div>` : ""}
      </div>
      <div class="stat-box" style="text-align:left">
        <strong>Phân tích nội dung</strong>
        <div style="margin-top:7px">${_escapeHtml(channelFit)} · độ khớp ${Math.round((analysis.niche_match_score || 0) * 100)}%</div>
        <div>Keyword chính: <b>${_escapeHtml(analysis.primary_keyword || "Chưa chọn vì ngoài ngách")}</b></div>
        <div>Hook score: <b>${Number(analysis.hook_score || 0)}/100</b></div>
        <div class="muted-help">${_escapeHtml(analysis.summary || "")}</div>
        ${Array.isArray(analysis.thumbnail_concepts) && analysis.thumbnail_concepts.length ? `<div class="muted-help" style="margin-top:8px">Concept thumbnail: ${analysis.thumbnail_concepts.map(item => _escapeHtml(`${item.label || item.concept}: ${item.text || ''}`)).join(' · ')}</div>` : ""}
      </div>
      <div>
        <label>YouTube · Tiêu đề đề xuất</label>
        <div style="display:flex;gap:8px"><input id="pack-youtube-title" value="${_escapeHtml(yt.recommended_title || "")}" readonly><button class="btn secondary small" id="pack-copy-title">Copy</button></div>
        <div style="display:grid;gap:6px;margin-top:8px">${(yt.alternative_titles || []).map(item => `<button class="btn secondary small pack-alt-title" data-title="${_escapeHtml(item)}" style="text-align:left">${_escapeHtml(item)}</button>`).join("")}</div>
      </div>
      <div><label>Mô tả YouTube</label><textarea id="pack-youtube-description" rows="10" readonly>${_escapeHtml(yt.description || "")}</textarea></div>
      <div>
        <label>Thumbnail YouTube</label>
        ${_publishingThumbnailGrid(yt.thumbnail_urls || [])}
        ${(components.youtube_thumbnails || {}).status === "failed" ? `<button class="btn gradient small" style="margin-top:8px" data-pack-retry="youtube_thumbnails" data-job-id="${jobId}">🔄 Tạo lại Thumbnail YouTube hook hơn</button>` : ""}
      </div>
      <div><label>Facebook caption</label><textarea id="pack-facebook-caption" rows="6" readonly>${_escapeHtml(fb.caption || "")}</textarea></div>
      <div>
        <label>Thumbnail Facebook</label>
        ${_publishingThumbnailGrid(fb.thumbnail_urls || [])}
        ${(components.facebook_thumbnails || {}).status === "failed" ? `<button class="btn gradient small" style="margin-top:8px" data-pack-retry="facebook_thumbnails" data-job-id="${jobId}">🔄 Tạo lại Thumbnail Facebook hook hơn</button>` : ""}
      </div>
      ${(components.publish_ready || {}).status === "failed" ? `<div class="stat-box" style="text-align:left;color:var(--err)">publish_ready.mp4 chưa tạo được. <button class="btn secondary small" data-pack-retry="publish_ready" data-job-id="${jobId}">Tạo lại publish_ready.mp4</button></div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="btn gradient" href="${pack.download_url}">Tải toàn bộ Publishing Pack</a>
        ${pack.publish_ready_video_url ? `<a class="btn secondary" href="${pack.publish_ready_video_url}?download=true">Tải publish_ready.mp4</a>` : ""}
        <button class="btn secondary" id="pack-use-publish">Dùng bộ này để đăng</button>
      </div>
      ${_publishingManualRegenerate(jobId, components)}`;
    if ($("#pack-copy-title")) $("#pack-copy-title").onclick = () => _copyText(yt.recommended_title || "");
    content.querySelectorAll(".pack-alt-title").forEach(btn => btn.onclick = () => _copyText(btn.dataset.title));
    if ($("#pack-use-publish")) $("#pack-use-publish").onclick = () => {
      modal.classList.add("hidden");
      openPublish(jobId);
    };
    _wirePublishingRetryButtons(jobId, content);
  } catch (e) {
    $("#publishing-pack-status").textContent = e.message;
    content.innerHTML = `<div style="color:var(--err)">${_escapeHtml(e.message)}</div>`;
  }
}
if ($("#publishing-pack-close")) $("#publishing-pack-close").onclick = () => $("#publishing-pack-modal").classList.add("hidden");
if ($("#publishing-pack-modal")) $("#publishing-pack-modal").addEventListener("click", ev => {
  if (ev.target === $("#publishing-pack-modal")) $("#publishing-pack-modal").classList.add("hidden");
});

// ---------------- preview + publish ----------------
function openPreview(jobId) {
  currentJobId = jobId;
  const sourceVideo = $("#preview-source-video");
  $("#before-video-pane").classList.remove("hidden");
  $("#before-video-note").classList.add("hidden");
  sourceVideo.src = `/api/jobs/${jobId}/source-video`;
  sourceVideo.onerror = () => {
    $("#before-video-pane").classList.add("hidden");
    $("#before-video-note").classList.remove("hidden");
  };
  $("#preview-video").src = `/api/jobs/${jobId}/video`;
  $("#preview-download").href = `/api/jobs/${jobId}/video?download=true`;
  $("#prepublish-check-btn").dataset.jobId = jobId;
  $("#preview-improve-btn").dataset.jobId = jobId;
  $("#prepublish-result").classList.add("hidden");
  $("#prepublish-result").innerHTML = "";
  $("#preview-modal").classList.remove("hidden");
  $("#preview-close").focus();
}

function closePreview() {
  $("#preview-modal").classList.add("hidden");
  $("#preview-video").pause();
  $("#preview-video").src = "";
  $("#preview-source-video").pause();
  $("#preview-source-video").src = "";
}

$("#preview-close").onclick = closePreview;
$("#preview-modal").addEventListener("click", ev => {
  if (ev.target === $("#preview-modal")) closePreview();
});

document.addEventListener("keydown", ev => {
  const modal = $("#preview-modal");
  if (modal.classList.contains("hidden")) return;
  const target = ev.target;
  const isEditing = target && (
    target.matches?.("input, textarea, select") || target.isContentEditable
  );
  if (ev.key === "Escape") {
    ev.preventDefault();
    closePreview();
    return;
  }
  if ((ev.code === "Space" || ev.key === " ") && !isEditing) {
    ev.preventDefault();
    const video = $("#preview-video");
    if (video.paused || video.ended) video.play();
    else video.pause();
  }
});

$("#prepublish-check-btn").onclick = async () => {
  const box = $("#prepublish-result");
  box.classList.remove("hidden");
  box.innerHTML = `<span style="color:var(--text-dim)">Đang đọc thông số video...</span>`;
  try {
    const report = await api(`/api/jobs/${$("#prepublish-check-btn").dataset.jobId}/prepublish-check`);
    const facts = report.facts;
    const issues = report.issues.length ? report.issues.map(item =>
      `<div style="color:${item.severity === 'error' ? 'var(--err)' : 'var(--warn)'};margin-top:5px">• ${_escapeHtml(item.message)}</div>`
    ).join("") : `<div style="color:var(--accent);margin-top:5px">Không phát hiện lỗi kỹ thuật cơ bản.</div>`;
    const platforms = report.platforms.map(item =>
      `<div style="margin-top:5px"><b>${_escapeHtml(item.platform)} · ${_escapeHtml(item.format_name)}</b>: ` +
      `<span style="color:${item.status === 'ready' ? 'var(--accent)' : 'var(--warn)'}">${item.status === 'ready' ? 'Phù hợp' : 'Cần điều chỉnh'}</span>` +
      `<div style="font-size:12px;color:var(--text-dim)">${_escapeHtml(item.message)}</div></div>`
    ).join("");
    box.innerHTML = `<div style="font-weight:600">${report.ready ? '✓ Có thể tiếp tục kiểm tra bằng mắt' : '⚠ Có lỗi cần xử lý'}</div>
      <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${facts.width}×${facts.height} · ${facts.fps} FPS · ${facts.duration}s · ${facts.video_codec}/${facts.audio_codec || 'không audio'}</div>
      ${issues}<div style="margin-top:10px;font-weight:600">Độ phù hợp nền tảng</div>${platforms}`;
  } catch (e) {
    box.innerHTML = `<span style="color:var(--err)">${_escapeHtml(e.message)}</span>`;
  }
};

function _qualitySeverityColor(severity) {
  if (severity === "error") return "var(--err)";
  if (severity === "warning") return "var(--warn)";
  return "var(--text-dim)";
}

function renderQualityReport(report) {
  const findings = report.findings || [];
  const actions = report.next_actions || [];
  const summary = report.summary || [];
  const facts = (report.prepublish || {}).facts || {};
  return `
    <div style="display:grid;grid-template-columns:120px minmax(0,1fr);gap:14px;align-items:center">
      <div class="stat-box" style="margin:0;text-align:center"><div class="num">${report.score}</div><div class="label">Quality score</div></div>
      <div>
        <div style="font-size:16px;font-weight:700;margin-bottom:6px">${_escapeHtml(report.verdict || "")}</div>
        <div style="font-size:12px;color:var(--text-dim)">${summary.map(_escapeHtml).join(" · ")}</div>
      </div>
    </div>
    <div class="row">
      <div>
        <label>Findings</label>
        <div style="display:grid;gap:8px">
          ${findings.length ? findings.map(item => `
            <div style="padding:10px 12px;background:var(--panel-2);border:1px solid var(--border);border-radius:8px">
              <div style="font-size:12px;font-weight:700;color:${_qualitySeverityColor(item.severity)}">${_escapeHtml(item.severity.toUpperCase())} · ${_escapeHtml(item.category)}</div>
              <div style="font-size:13px;margin-top:4px">${_escapeHtml(item.message)}</div>
              <div style="font-size:12px;color:var(--text-dim);margin-top:5px">${_escapeHtml(item.action)}</div>
            </div>
          `).join("") : `<div style="font-size:13px;color:var(--ok)">No automated findings. Still watch the video once before publishing.</div>`}
        </div>
      </div>
      <div>
        <label>Next actions</label>
        <ul class="research-list">${actions.map(item => `<li>${_escapeHtml(item)}</li>`).join("")}</ul>
        <label style="margin-top:14px">Platform facts</label>
        <div style="font-size:12px;color:var(--text-dim);line-height:1.6">
          ${facts.width || 0}x${facts.height || 0}<br>
          ${facts.duration || 0}s · ${facts.fps || 0} FPS<br>
          ${_escapeHtml(facts.video_codec || "unknown")} / ${_escapeHtml(facts.audio_codec || "no audio")}
        </div>
      </div>
    </div>
  `;
}

async function openQualityReview(jobId) {
  $("#quality-report-box").innerHTML = `<div style="color:var(--text-dim)">Dang review video...</div>`;
  $("#quality-modal").classList.remove("hidden");
  try {
    const report = await api(`/api/jobs/${jobId}/quality-review`);
    $("#quality-report-box").innerHTML = renderQualityReport(report);
  } catch (e) {
    $("#quality-report-box").innerHTML = `<div style="color:var(--err)">${_escapeHtml(e.message)}</div>`;
  }
}

$("#quality-close").onclick = () => $("#quality-modal").classList.add("hidden");
$("#quality-modal").addEventListener("click", ev => {
  if (ev.target === $("#quality-modal")) $("#quality-modal").classList.add("hidden");
});
$("#preview-improve-btn").onclick = () => openQualityReview($("#preview-improve-btn").dataset.jobId);
$("#latest-improve-btn").onclick = () => openQualityReview($("#latest-improve-btn").dataset.jobId);

$("#preview-publish-btn").onclick = () => {
  closePreview();
  openPublish(currentJobId);
};

function openPublish(jobId) {
  currentJobId = jobId;
  $("#publish-results").innerHTML = "";
  $("#publish-modal").classList.remove("hidden");
  loadConnections();
  _prefillPublishSuggestions(jobId);
  loadScheduledPosts();
}
$("#publish-close").onclick = () => $("#publish-modal").classList.add("hidden");

// Pre-fills the title from the job's translated text and suggests a
// handful of hashtag chips derived from its most distinctive words — a
// starting point to tweak, not meant to be used verbatim untouched.
const _VI_STOPWORDS = new Set([
  "và","của","là","có","cho","trong","với","một","này","đã","không","được",
  "cũng","để","khi","như","thì","tôi","bạn","anh","chị","nó","đó","các","những",
  "vì","nên","nhưng","mà","về","trên","dưới","ra","vào","lại","rồi","sẽ","đang",
]);

function _suggestHashtags(text, max = 5) {
  if (!text) return [];
  const words = text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter(w => w.length >= 3 && !_VI_STOPWORDS.has(w));
  const freq = {};
  for (const w of words) freq[w] = (freq[w] || 0) + 1;
  return Object.entries(freq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
    .map(([w]) => "#" + w.replace(/\s+/g, ""));
}

async function _prefillPublishSuggestions(jobId) {
  const job = (window._jobsById || {})[jobId];
  if (!job) return;
  let baseText = job.title || "";
  let suggestedHashtags = [];
  if (job.has_publishing_pack) {
    try {
      const pack = await api(`/api/jobs/${jobId}/publishing-pack`);
      window._publishingPackByJob = window._publishingPackByJob || {};
      window._publishingPackByJob[jobId] = pack;
      const yt = pack.youtube || {};
      $("#publish-title").value = yt.recommended_title || baseText;
      $("#publish-desc").value = yt.description || "";
      $("#publish-hashtags").value = (yt.hashtags || []).join(" ");
      baseText = yt.recommended_title || baseText;
      suggestedHashtags = yt.hashtags || [];
    } catch (e) {
      console.warn("Could not prefill Publishing Pack", e);
    }
  }
  if (!$("#publish-title").value) $("#publish-title").value = baseText;
  if (!suggestedHashtags.length) suggestedHashtags = _suggestHashtags(baseText);
  const box = $("#publish-hashtag-suggestions");
  if (!suggestedHashtags.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<span style="font-size:12px;color:var(--text-dim)">Gợi ý: </span>` +
    suggestedHashtags.map(tag => `<span class="hashtag-chip" data-tag="${_escapeHtml(tag)}">${_escapeHtml(tag)}</span>`).join(" ");
  box.querySelectorAll(".hashtag-chip").forEach(chip => {
    chip.onclick = () => {
      const current = $("#publish-hashtags").value.trim();
      const tag = chip.dataset.tag;
      if (!current.includes(tag)) {
        $("#publish-hashtags").value = current ? `${current} ${tag}` : tag;
      }
    };
  });
}

// Feature tab switching - ensure DOM is loaded
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".feature-tab").forEach(tab => {
    tab.onclick = () => {
      const feature = tab.dataset.feature;

      console.log("Tab clicked:", feature);

      // Update tab active state
      document.querySelectorAll(".feature-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      // Update panel visibility
      document.querySelectorAll(".feature-panel").forEach(panel => {
        panel.classList.remove("active");
        if (panel.dataset.feature === feature) {
          panel.classList.add("active");
          console.log("Panel shown:", feature);

          // Initialize Content OS if switching to that tab
          if (feature === "content-os") {
            initContentOS();
          }
        }
      });
    };
  });
});

// ---------------- Content OS ----------------
let contentOSProjects = [];
let contentOSRuns = [];
let selectedProjectId = null;
let selectedRunId = null;

async function initContentOS() {
  console.log("Initializing Content OS");

  try {
    const health = await api("/api/content-os/health");
    console.log("Content OS health:", health);

    if (!health.enabled) {
      $("#content-os-feature-disabled").classList.remove("hidden");
      $("#content-os-enabled").classList.add("hidden");
      return;
    }

    $("#content-os-feature-disabled").classList.add("hidden");
    $("#content-os-enabled").classList.remove("hidden");

    // Load projects
    await loadContentOSProjects();
  } catch (e) {
    console.error("Failed to initialize Content OS:", e);
    $("#content-os-status").textContent = "Lỗi kết nối";
  }
}

async function loadContentOSProjects() {
  try {
    contentOSProjects = await api("/api/content-os/projects");
    renderContentOSProjects();
  } catch (e) {
    console.error("Failed to load projects:", e);
    $("#content-os-projects-list").innerHTML = `<div style="color:var(--err);font-size:13px">Lỗi tải dự án: ${e.message}</div>`;
  }
}

function renderContentOSProjects() {
  const list = $("#content-os-projects-list");
  const select = $("#content-os-project-select");

  if (contentOSProjects.length === 0) {
    list.innerHTML = `<div style="font-size: 13px; color: var(--text-dim);">Chưa có dự án nào.</div>`;
    select.innerHTML = `<option value="">-- Chọn dự án --</option>`;
    return;
  }

  list.innerHTML = contentOSProjects.map(p => `
    <div style="padding: 8px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; justify-content: space-between; align-items: center;" onclick="selectContentOSProject(${p.id})">
      <div>
        <div style="font-weight: 600; font-size: 13px;">${escapeHtml(p.channel_name)}</div>
        <div style="font-size: 12px; color: var(--text-dim);">${escapeHtml(p.topic)}</div>
        <div style="font-size: 11px; color: var(--text-dim);">Định dạng: ${p.content_format}</div>
      </div>
      <button class="btn secondary small" onclick="event.stopPropagation(); deleteContentOSProject(${p.id})" style="margin-left: 8px;">🗑️</button>
    </div>
  `).join("");

  select.innerHTML = `<option value="">-- Chọn dự án --</option>` +
    contentOSProjects.map(p => `<option value="${p.id}">${escapeHtml(p.channel_name)} - ${escapeHtml(p.topic)}</option>`).join("");
}

function selectContentOSProject(projectId) {
  selectedProjectId = projectId;
  $("#content-os-project-select").value = projectId;
  $("#content-os-create-run-btn").disabled = false;

  // Load runs for this project
  loadContentOSRuns(projectId);
}

async function deleteContentOSProject(projectId) {
  if (!confirm("Bạn có chắc muốn xóa dự án này và tất cả runs liên quan?")) return;

  try {
    await api(`/api/content-os/projects/${projectId}`, { method: "DELETE" });
    await loadContentOSProjects();

    // Clear selection if deleted project was selected
    if (selectedProjectId === projectId) {
      selectedProjectId = null;
      $("#content-os-project-select").value = "";
      $("#content-os-create-run-btn").disabled = true;
      contentOSRuns = [];
      renderContentOSRuns();
    }
  } catch (e) {
    console.error("Failed to delete project:", e);
    alert(`Lỗi xóa dự án: ${e.message}`);
  }
}

async function loadContentOSRuns(projectId) {
  try {
    contentOSRuns = await api(`/api/content-os/runs?project_id=${projectId}`);
    renderContentOSRuns();
  } catch (e) {
    console.error("Failed to load runs:", e);
    $("#content-os-runs-list").innerHTML = `<div style="color:var(--err);font-size:13px">Lỗi tải run: ${e.message}</div>`;
  }
}

function renderContentOSRuns() {
  const list = $("#content-os-runs-list");

  if (contentOSRuns.length === 0) {
    list.innerHTML = `<div style="font-size: 13px; color: var(--text-dim);">Chưa có run nào.</div>`;
    return;
  }

  list.innerHTML = contentOSRuns.map(r => `
    <div style="padding: 8px; border-bottom: 1px solid var(--border);">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="cursor: pointer; flex: 1;" onclick="selectContentOSRun(${r.id})">
          <div style="font-weight: 600; font-size: 13px;">Run #${r.id}</div>
          <div style="font-size: 12px; color: var(--text-dim);">Giai đoạn: ${escapeHtml(r.current_stage)}</div>
          <div style="font-size: 11px; color: var(--text-dim);">Tiến độ: ${r.progress_percent}%</div>
        </div>
        <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 4px;">
          <div style="font-size: 11px; color: ${getRunStatusColor(r.status)}; font-weight: 600;">${escapeHtml(r.status)}</div>
          <div style="display: flex; gap: 4px;">
            ${r.status === 'created' ? `<button class="btn secondary small" onclick="event.stopPropagation(); startContentOSRun(${r.id})">▶️</button>` : ''}
            ${r.status === 'running' ? `<button class="btn secondary small" onclick="event.stopPropagation(); cancelContentOSRun(${r.id})">⏸️</button>` : ''}
            ${r.current_stage === 'awaiting_approval' ? `<button class="btn gradient small" onclick="event.stopPropagation(); approveContentOSRun(${r.id})">✅</button>` : ''}
            ${canCreateContentOSJob(r) ? `<button class="btn gradient small" onclick="event.stopPropagation(); createContentOSJob(${r.id})">🎬</button>` : ''}
          </div>
        </div>
      </div>
    </div>
  `).join("");
}

function getRunStatusColor(status) {
  const colors = {
    "created": "var(--text-dim)",
    "running": "var(--accent)",
    "paused": "var(--warn)",
    "completed": "var(--ok)",
    "failed": "var(--err)",
    "cancelled": "var(--text-dim)",
  };
  return colors[status] || "var(--text-dim)";
}

function canCreateContentOSJob(run) {
  return [
    "approved",
    "ready_for_localization",
    "storyboarding",
    "awaiting_storyboard_approval",
    "asset_planning",
    "asset_resolving",
    "assets_ready",
    "voice_generation",
    "subtitle_generation",
    "timeline_building",
    "rendering",
    "output_validation",
    "completed",
    "failed",
  ].includes(run.current_stage);
}

function selectContentOSRun(runId) {
  selectedRunId = runId;
  const run = contentOSRuns.find(r => r.id === runId);

  if (run) {
    $("#content-os-run-actions").style.display = "block";
    $("#content-os-run-status").innerHTML = `
      <div><strong>Run #${run.id}</strong></div>
      <div>Giai đoạn: ${escapeHtml(run.current_stage)}</div>
      <div>Trạng thái: ${escapeHtml(run.status)}</div>
      <div>Tiến độ: ${run.progress_percent}%</div>
    `;

    // Remove existing view script button if any
    const existingBtn = document.querySelector("#content-os-view-script-btn");
    if (existingBtn) existingBtn.remove();

    // Add view script button if script is available
    if (run.current_stage === "awaiting_approval" || canCreateContentOSJob(run)) {
      const scriptBtn = document.createElement("button");
      scriptBtn.id = "content-os-view-script-btn";
      scriptBtn.className = "btn secondary";
      scriptBtn.textContent = "📄 Xem Script";
      scriptBtn.onclick = () => viewContentOSScript(run.id);
      $("#content-os-run-actions").appendChild(scriptBtn);
    }

    // Enable/disable buttons based on run state
    $("#content-os-start-run-btn").disabled = run.status === "running" || run.status === "completed";
    $("#content-os-cancel-run-btn").disabled = run.status === "completed" || run.status === "cancelled";
    $("#content-os-approve-run-btn").disabled = run.current_stage !== "awaiting_approval" && run.current_stage !== "script_audit";
    $("#content-os-create-job-btn").disabled = !canCreateContentOSJob(run);
  }
}

async function viewContentOSScript(runId) {
  try {
    console.log("Fetching script for run:", runId);
    const script = await api(`/api/content-os/runs/${runId}/artifacts/script`);
    console.log("Script data received:", script);

    if (!script) {
      alert("Không tìm thấy script cho run này");
      return;
    }

    // The actual script data is in the 'data' field
    const scriptContent = script.data || script;
    console.log("Script content:", scriptContent);

    // Display script in the run detail area instead of modal
    const scriptDiv = document.createElement("div");
    scriptDiv.id = "content-os-script-display";
    scriptDiv.style.cssText = "margin-top: 20px; padding: 15px; background: #f9f9f9; border-radius: 8px; border: 1px solid #ddd; color: #333;";

    scriptDiv.innerHTML = `
      <h3 style="margin-top: 0; color: #333;">📄 Script Details</h3>
      <div style="margin-bottom: 15px; color: #333;">
        <strong>Title:</strong> ${escapeHtml(scriptContent.title_options?.[0] || scriptContent.title || "N/A")}
      </div>
      <div style="margin-bottom: 15px; color: #333;">
        <strong>Narration:</strong><br>
        ${escapeHtml(scriptContent.narration_text || scriptContent.narration || "N/A")}
      </div>
      <div style="margin-bottom: 15px; color: #333;">
        <strong>Segments:</strong>
        ${scriptContent.segments && scriptContent.segments.length > 0 ? 
          scriptContent.segments.map((seg, i) => `
            <div style="margin: 10px 0; padding: 10px; background: #fff; border-radius: 4px; border: 1px solid #eee; color: #333;">
              <div><strong>Segment ${i + 1}:</strong></div>
              <div>Start: ${seg.start_second}s - End: ${seg.end_second}s</div>
              <div>Narration: ${escapeHtml(seg.narration || "")}</div>
              <div>Subtitle: ${escapeHtml(seg.subtitle_text || "")}</div>
              <div>Visual: ${escapeHtml(seg.visual_instruction || "")}</div>
            </div>
          `).join('') : 
          '<div style="color: #666;">No segments available</div>'
        }
      </div>
      <div style="margin-top: 15px;">
        <button class="btn secondary" onclick="document.getElementById('content-os-script-display').remove()">Đóng</button>
      </div>
    `;

    // Remove existing script display if any
    const existing = document.getElementById("content-os-script-display");
    if (existing) existing.remove();

    // Add to run detail area
    $("#content-os-run-actions").appendChild(scriptDiv);

  } catch (e) {
    console.error("Failed to load script:", e);
    alert(`Lỗi tải script: ${e.message}`);
  }
}

// Content OS event handlers
$("#content-os-create-project-btn").onclick = async () => {
  const channelName = $("#content-os-channel-name").value.trim();
  const topic = $("#content-os-topic").value.trim();
  const targetPlatform = $("#content-os-target-platforms").value;
  const targetMarket = $("#content-os-target-market").value.trim();
  const targetLanguage = $("#content-os-target-language").value;
  const duration = parseInt($("#content-os-duration").value);
  const format = $("#content-os-format").value;
  const instructions = $("#content-os-instructions").value.trim();
  const contentBrandingEnabled = $("#content-os-branding-enable").checked;
  const contentBrandingText = $("#content-os-branding-text").value.trim() || channelName;
  const contentBranding = {
    enabled: contentBrandingEnabled,
    text: contentBrandingText,
    preset: $("#content-os-branding-preset").value,
    edge_runner_enabled: true,
    diagonal_enabled: true,
    pattern_enabled: true,
    fingerprint_enabled: true,
    avoid_subtitles: true,
    avoid_center: true,
  };

  if (!channelName || !topic) {
    $("#content-os-project-error").textContent = "Vui lòng nhập tên kênh và chủ đề.";
    return;
  }

  try {
    const project = await api("/api/content-os/projects", {
      method: "POST",
      body: JSON.stringify({
        channel_name: channelName,
        topic: topic,
        target_platforms: [targetPlatform],
        source_platforms: ["youtube"],
        target_market: targetMarket,
        target_language: targetLanguage,
        target_duration_seconds: duration,
        content_format: format,
        max_source_items: 10,
        user_instructions: instructions,
        auto_download_sources: false,
        branding_config: contentBranding,
      }),
    });

    $("#content-os-project-error").textContent = "";
    $("#content-os-channel-name").value = "";
    $("#content-os-topic").value = "";
    $("#content-os-instructions").value = "";

    await loadContentOSProjects();
  } catch (e) {
    $("#content-os-project-error").textContent = `Lỗi: ${e.message}`;
  }
};

$("#content-os-project-select").onchange = (e) => {
  const projectId = parseInt(e.target.value);
  if (projectId) {
    selectContentOSProject(projectId);
    $("#content-os-create-run-btn").disabled = false;
  } else {
    $("#content-os-create-run-btn").disabled = true;
  }
};

$("#content-os-create-run-btn").onclick = async () => {
  if (!selectedProjectId) return;

  try {
    const run = await api("/api/content-os/runs", {
      method: "POST",
      body: JSON.stringify({ project_id: selectedProjectId }),
    });

    await loadContentOSRuns(selectedProjectId);
    selectContentOSRun(run.id);
  } catch (e) {
    console.error("Failed to create run:", e);
    alert(`Lỗi tạo run: ${e.message}`);
  }
};

let contentOSPollingInterval = null;

$("#content-os-start-run-btn").onclick = async () => {
  if (!selectedRunId) return;

  try {
    await api(`/api/content-os/runs/${selectedRunId}/start`, { method: "POST" });
    await loadContentOSRuns(selectedProjectId);
    selectContentOSRun(selectedRunId);

    // Start polling for updates
    startContentOSPolling();
  } catch (e) {
    console.error("Failed to start run:", e);
    alert(`Lỗi chạy run: ${e.message}`);
  }
};

function startContentOSPolling() {
  if (contentOSPollingInterval) {
    clearInterval(contentOSPollingInterval);
  }

  contentOSPollingInterval = setInterval(async () => {
    if (!selectedProjectId) return;

    try {
      await loadContentOSRuns(selectedProjectId);

      // Check if run is still running
      const run = contentOSRuns.find(r => r.id === selectedRunId);
      if (run && (run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled')) {
        stopContentOSPolling();
      }
    } catch (e) {
      console.error("Polling error:", e);
    }
  }, 2000); // Poll every 2 seconds
}

function stopContentOSPolling() {
  if (contentOSPollingInterval) {
    clearInterval(contentOSPollingInterval);
    contentOSPollingInterval = null;
  }
}

$("#content-os-cancel-run-btn").onclick = async () => {
  if (!selectedRunId) return;

  if (!confirm("Bạn có chắc muốn hủy run này?")) return;

  try {
    await api(`/api/content-os/runs/${selectedRunId}/cancel`, { method: "POST" });
    await loadContentOSRuns(selectedProjectId);
    selectContentOSRun(selectedRunId);
  } catch (e) {
    console.error("Failed to cancel run:", e);
    alert(`Lỗi hủy run: ${e.message}`);
  }
};

$("#content-os-approve-run-btn").onclick = async () => {
  if (!selectedRunId) return;

  try {
    await api(`/api/content-os/runs/${selectedRunId}/approve`, {
      method: "POST",
      body: JSON.stringify({
        approval_type: "script",
        decision: "approved",
        note: "",
      }),
    });
    await loadContentOSRuns(selectedProjectId);
    selectContentOSRun(selectedRunId);
  } catch (e) {
    console.error("Failed to approve run:", e);
    alert(`Lỗi phê duyệt: ${e.message}`);
  }
};

$("#content-os-create-job-btn").onclick = async () => {
  if (!selectedRunId) return;

  try {
    const result = await api(`/api/content-os/runs/${selectedRunId}/create-job`, {
      method: "POST",
      body: JSON.stringify({ source_url: null }),
    });

    alert(`Đã tạo job: ${result.job_id}\nJob sẽ hiển thị trong danh sách Jobs.`);
    // Refresh job list to show the new job
    refreshJobs();
  } catch (e) {
    console.error("Failed to create job:", e);
    alert(`Lỗi tạo job: ${e.message}`);
  }
};

document.querySelectorAll(".chip").forEach(chip => {
  chip.onclick = () => {
    const p = chip.dataset.platform;
    if (selectedPlatforms.has(p)) { selectedPlatforms.delete(p); chip.classList.remove("active"); }
    else { selectedPlatforms.add(p); chip.classList.add("active"); }
  };
});

const PLATFORM_LABEL = { tiktok: "TikTok", facebook: "Facebook Page", youtube: "YouTube" };

async function loadConnections() {
  const box = $("#connect-cards");
  box.innerHTML = `<div style="color:var(--text-dim);font-size:13px">Đang tải trạng thái kết nối…</div>`;
  let conns;
  try { conns = await api("/api/social/connections"); }
  catch (e) { box.innerHTML = ""; return; }

  box.innerHTML = Object.entries(conns).map(([platform, info]) => `
    <div class="connect-card" data-card="${platform}">
      <div class="connect-head">
        <div>
          <span class="connect-name">${PLATFORM_LABEL[platform]}</span>
          <span class="pill ${info.connected ? "on" : "off"}">${info.connected ? "Đã kết nối" : "Chưa kết nối"}</span>
        </div>
        ${info.connected
          ? `<button class="btn secondary small" data-disconnect="${platform}">Ngắt kết nối</button>`
          : `<button class="btn secondary small" data-connect="${platform}" ${info.configured ? "" : "disabled"}>Kết nối</button>`}
      </div>
      ${info.connected ? `<div class="connect-sub">Đăng dưới tên: ${info.account_name || "(không rõ tên)"}</div>` : ""}
      ${!info.configured ? `<div class="connect-note">${info.not_configured_message}</div>` : ""}
      <div class="connect-qr-box hidden" id="qr-${platform}"></div>
    </div>
  `).join("");

  box.querySelectorAll("[data-connect]").forEach(btn => {
    btn.onclick = () => startConnect(btn.dataset.connect);
  });
  box.querySelectorAll("[data-disconnect]").forEach(btn => {
    btn.onclick = async () => {
      await fetch(`/api/social/connections/${btn.dataset.disconnect}`, { method: "DELETE" });
      loadConnections();
    };
  });
}

async function startConnect(platform) {
  let data;
  try { data = await api(`/api/social/connect/${platform}`); }
  catch (e) { alert(e.message); return; }

  window.open(data.authorize_url, "connect_" + platform, "width=520,height=680");

  const qrBox = $(`#qr-${platform}`);
  qrBox.classList.remove("hidden");
  qrBox.innerHTML = `
    <img src="${data.qr_code_url}" width="140" height="140" alt="QR đăng nhập ${platform}">
    <div style="font-size:12px;color:var(--text-dim);max-width:260px">
      Cửa sổ đăng nhập ${PLATFORM_LABEL[platform]} đã mở. Nếu bạn đang dùng máy tính nhưng đã
      đăng nhập ${PLATFORM_LABEL[platform]} trên điện thoại, quét mã QR này bằng điện thoại để
      xác nhận nhanh hơn — không cần gõ lại mật khẩu trên máy tính.
    </div>`;
}

$("#publish-submit").onclick = async () => {
  if (selectedPlatforms.size === 0) { alert("Chọn ít nhất 1 nền tảng"); return; }
  const hashtags = $("#publish-hashtags").value.split(/\s+/).filter(Boolean);
  $("#publish-results").innerHTML = "Đang đăng...";
  try {
    const { results } = await api(`/api/jobs/${currentJobId}/publish`, {
      method: "POST", body: JSON.stringify({
        platforms: [...selectedPlatforms],
        title: $("#publish-title").value,
        description: $("#publish-desc").value,
        hashtags,
      }),
    });
    $("#publish-results").innerHTML = results.map(r =>
      `<div class="publish-result ${r.success ? "ok" : "fail"}"><b>${r.platform}:</b> ${r.message}</div>`
    ).join("");
  } catch (e) {
    $("#publish-results").innerHTML = `<div class="publish-result fail">${e.message}</div>`;
  }
};

async function loadScheduledPosts() {
  try {
    const rows = await api("/api/scheduled-posts");
    $("#scheduled-posts").innerHTML = rows.slice(0, 5).map(r => `
      <div class="connect-card"><b>${escapeHtml(r.title)}</b><div class="connect-sub">
      ${r.platforms.map(p => PLATFORM_LABEL[p] || p).join(", ")} · ${new Date(r.scheduled_at * 1000).toLocaleString(uiLocale())} · ${r.status}
      </div>${r.status === "pending" ? `<button class="btn danger small" data-cancel-schedule="${r.id}" style="margin-top:8px">Huỷ lịch</button>` : ""}</div>`).join("");
    $("#scheduled-posts").querySelectorAll("[data-cancel-schedule]").forEach(btn => {
      btn.onclick = async () => {
        await api(`/api/scheduled-posts/${btn.dataset.cancelSchedule}`, { method: "DELETE" });
        loadScheduledPosts();
      };
    });
  } catch { $("#scheduled-posts").innerHTML = ""; }
}

$("#publish-schedule").onclick = async () => {
  if (selectedPlatforms.size === 0) { alert("Chọn ít nhất 1 nền tảng"); return; }
  const localValue = $("#publish-scheduled-at").value;
  if (!localValue) { alert("Chọn ngày và giờ đăng"); return; }
  const scheduledAt = new Date(localValue).getTime() / 1000;
  try {
    await api(`/api/jobs/${currentJobId}/schedule-publish`, {
      method: "POST", body: JSON.stringify({
        platforms: [...selectedPlatforms], title: $("#publish-title").value,
        description: $("#publish-desc").value,
        hashtags: $("#publish-hashtags").value.split(/\s+/).filter(Boolean),
        scheduled_at: scheduledAt,
      }),
    });
    $("#publish-results").innerHTML = `<div class="publish-result ok">Đã lên lịch đăng tự động.</div>`;
    loadScheduledPosts();
  } catch (e) { $("#publish-results").innerHTML = `<div class="publish-result fail">${escapeHtml(e.message)}</div>`; }
};

// ---------------- admin ----------------
$("#admin-btn").onclick = () => {
  $("#app-view").classList.add("hidden");
  $("#admin-view").classList.remove("hidden");
  loadAdmin();
};
$("#admin-back-btn").onclick = () => {
  $("#admin-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
};

async function loadAdmin() {
  const [stats, users, feedback, topups] = await Promise.all([
    api("/api/admin/stats"), api("/api/admin/users"), api("/api/admin/feedback"), api("/api/admin/top-up-requests"),
  ]);

  $("#admin-stats").innerHTML = `
    <div class="stat-box"><div class="num">${stats.total_users}</div><div class="label">Người dùng</div></div>
    <div class="stat-box"><div class="num">${stats.total_jobs}</div><div class="label">Tổng số video</div></div>
    <div class="stat-box"><div class="num">${stats.jobs_last_7d}</div><div class="label">Video (7 ngày qua)</div></div>
    <div class="stat-box"><div class="num">${stats.jobs_by_status.done || 0}</div><div class="label">Hoàn tất</div></div>
    <div class="stat-box"><div class="num">${stats.jobs_by_status.error || 0}</div><div class="label">Lỗi</div></div>
    <div class="stat-box"><div class="num">${Object.values(stats.publishes_by_platform).reduce((a,b)=>a+b,0)}</div><div class="label">Đã đăng lên MXH</div></div>
  `;

  const body = $("#admin-users-body");
  body.innerHTML = users.map(u => `
    <tr>
      <td>${u.username}</td>
      <td>${u.is_admin ? "Admin" : "User"}</td>
      <td>${u.credits}</td>
      <td>
        <div class="credit-adjust">
          <input type="number" id="credit-input-${u.id}" placeholder="+/-">
          <button class="btn secondary small" data-adjust="${u.id}">Áp dụng</button>
        </div>
      </td>
    </tr>
  `).join("");

  body.querySelectorAll("[data-adjust]").forEach(btn => {
    btn.onclick = async () => {
      const uid = btn.dataset.adjust;
      const val = parseInt($(`#credit-input-${uid}`).value, 10);
      if (Number.isNaN(val)) return;
      await api(`/api/admin/users/${uid}/credits`, { method: "POST", body: JSON.stringify({ delta: val }) });
      loadAdmin();
    };
  });

  $("#admin-topups-empty").classList.toggle("hidden", topups.length > 0);
  $("#admin-topups-body").innerHTML = topups.map(r => `
    <tr>
      <td>${r.username || ("#" + r.user_id)}</td>
      <td>${r.credits} token</td>
      <td>${_formatVnd(r.amount_vnd)}</td>
      <td style="max-width:260px;white-space:pre-wrap">${_escapeHtml(r.note || "")}</td>
      <td class="status-${r.status}">${_topupStatusLabel(r.status)}</td>
      <td>
        ${r.status === "pending" ? `
          <button class="btn secondary small" data-topup-approve="${r.id}">Duyệt</button>
          <button class="btn secondary small" data-topup-reject="${r.id}">Từ chối</button>
        ` : ""}
      </td>
    </tr>
  `).join("");
  $("#admin-topups-body").querySelectorAll("[data-topup-approve]").forEach(btn => {
    btn.onclick = async () => {
      await api(`/api/admin/top-up-requests/${btn.dataset.topupApprove}/approve`, {
        method: "POST", body: JSON.stringify({ admin_note: null }),
      });
      loadAdmin();
    };
  });
  $("#admin-topups-body").querySelectorAll("[data-topup-reject]").forEach(btn => {
    btn.onclick = async () => {
      const note = prompt("Lý do từ chối (không bắt buộc):") || null;
      await api(`/api/admin/top-up-requests/${btn.dataset.topupReject}/reject`, {
        method: "POST", body: JSON.stringify({ admin_note: note }),
      });
      loadAdmin();
    };
  });

  $("#admin-feedback-empty").classList.toggle("hidden", feedback.length > 0);
  $("#admin-feedback-body").innerHTML = feedback.map(f => `
    <tr>
      <td><b>${_escapeHtml(f.username || "(ẩn danh)")}</b><br><span style="color:var(--text-dim);font-size:12px">${_escapeHtml(f.email || f.phone || "Không có liên hệ")}</span></td>
      <td style="max-width:400px;white-space:pre-wrap">${_escapeHtml(f.message)}</td>
      <td>${f.page || ""}</td>
      <td>${new Date(f.created_at * 1000).toLocaleString(uiLocale())}</td>
    </tr>
  `).join("");
}

function _escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

$("#new-user-submit").onclick = async () => {
  $("#new-user-error").textContent = "";
  try {
    await api("/api/admin/users", { method: "POST", body: JSON.stringify({
      username: $("#new-user-username").value.trim(),
      password: $("#new-user-password").value,
      credits: parseInt($("#new-user-credits").value, 10) || 0,
    })});
    $("#new-user-username").value = "";
    $("#new-user-password").value = "";
    loadAdmin();
  } catch (e) { $("#new-user-error").textContent = e.message; }
};

initAuth();