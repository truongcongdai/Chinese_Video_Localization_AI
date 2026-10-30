"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8");
function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `Missing source section: ${start}`);
  return source.slice(first, last);
}

function element() {
  const classes = new Set();
  return {
    value: "", textContent: "", innerHTML: "", disabled: false, dataset: {}, children: [], style: {},
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
    },
    appendChild(child) { this.children.push(child); },
    querySelectorAll: () => [],
    addEventListener() {},
  };
}

function setup(extra = {}) {
  const elements = {};
  const context = vm.createContext({
    $: selector => elements[selector] ||= element(),
    document: {createElement: element, querySelectorAll: () => []},
    URL, URLSearchParams, console, uiLocale: () => "en-US", window: {},
    fetch() { throw new Error("Real transport is forbidden in frontend tests"); },
    ...extra,
  });
  vm.runInContext(section("const escapeHtml", "let currentJobId") +
    section("function _escapeHtml(s)", '\n$("#new-user-submit")'), context);
  return {context, elements};
}

test("admin renders feedback pages and both username surfaces as text", async () => {
  const payload = '<img src=x onerror="alert(1)">';
  const {context, elements} = setup({
    _formatVnd: () => "100", _topupStatusLabel: () => "pending",
    api: async url => ({
      "/api/admin/stats": {jobs_by_status: {}, publishes_by_platform: {}},
      "/api/admin/users": [{id: 1, username: payload, credits: 0}],
      "/api/admin/feedback": [{username: payload, page: payload, message: payload, created_at: 0}],
      "/api/admin/top-up-requests": [{id: 1, username: payload, status: "pending", credits: 1}],
    }[url]),
  });
  vm.runInContext(section("async function loadAdmin()", "\nfunction _escapeHtml"), context);
  await context.loadAdmin();
  for (const id of ["#admin-users-body", "#admin-feedback-body", "#admin-topups-body"]) {
    assert.doesNotMatch(elements[id].innerHTML, /<img/);
    assert.match(elements[id].innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  }
});

test("history escapes stored titles and the source URL fallback", async () => {
  const payload = '<img src=x onerror="alert(1)">';
  const jobs = [
    {id: "a", title: payload, source_url: "https://example.test/video"},
    {id: "b", title: null, source_url: 'https://example.test/"' + payload},
  ].map(job => ({...job, target_language: "vi", status: "error", created_at: 0}));
  const {context, elements} = setup({
    api: async () => jobs, _dateToUnix: () => null,
    updateStats() {}, refreshJobQueueStatus() {}, updateHistorySelection() {},
    activeCreatorJobId: null, latestResultJobId: null, _lastKnownStatus: {},
    selectedHistoryJobs: new Set(), STATUS_LABEL: {error: "Error"}, jobProgressNote: () => "",
  });
  vm.runInContext(section("async function refreshJobs()", "\nfunction updateHistorySelection"), context);
  await context.refreshJobs();
  assert.equal(elements["#job-list"].children.length, 2);
  for (const row of elements["#job-list"].children) {
    assert.doesNotMatch(row.innerHTML, /<img/);
    assert.match(row.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  }
});

test("speaker and Publishing Pack attributes escape quotes without losing metadata", async () => {
  const speaker = 'speaker_0" autofocus onfocus="alert(1)';
  const title = 'A "quoted" title & more';
  const {context, elements} = setup({
    api: async () => ({segments: [{start: 0, end: 1, text: "Safe", speaker_id: speaker}]}),
    _publishingComponentGrid: () => "", _publishingManualRegenerate: () => "",
    _publishingThumbnailGrid: () => "", _wirePublishingRetryButtons() {},
  });
  vm.runInContext(section("async function openReview(jobId)", "\nfunction _collectReviewSegments"), context);
  await context.openReview("a");
  assert.match(elements["#review-segments-list"].innerHTML,
    /value="speaker_0&quot; autofocus onfocus=&quot;alert\(1\)"/);
  assert.equal(context._escapeHtml(null), "");
  assert.equal(context._escapeHtml("'\"<>&"), "&#39;&quot;&lt;&gt;&amp;");
  context.api = async () => ({youtube: {recommended_title: title, alternative_titles: [title]}});
  vm.runInContext(section("async function openPublishingPack(jobId)", '\nif ($("#publishing-pack-close"))'), context);
  await context.openPublishingPack("a");
  const markup = elements["#publishing-pack-content"].innerHTML;
  assert.match(markup, /value="A &quot;quoted&quot; title &amp; more"/);
  assert.match(markup, /data-title="A &quot;quoted&quot; title &amp; more"/);
});

test("Content OS row controls call the correct APIs even when another run is selected", async () => {
  const calls = [], selections = [];
  const {context, elements} = setup({
    contentOSRuns: [
      {id: 1, status: "created", current_stage: "created"},
      {id: 2, status: "running", current_stage: "research"},
      {id: 3, status: "paused", current_stage: "awaiting_approval"},
      {id: 4, status: "completed", current_stage: "approved"},
    ],
    selectedRunId: 99, selectedProjectId: 10,
    api: async (url, opts) => { calls.push([url, opts]); return {job_id: "new-job"}; },
    loadContentOSRuns: async () => {}, selectContentOSRun: id => selections.push(id),
    startContentOSPolling() {}, refreshJobs() {}, alert() {}, confirm: () => true,
    event: {stopPropagation() {}},
  });
  vm.runInContext(section("function renderContentOSRuns()", "\nfunction selectContentOSRun") +
    section("async function startContentOSRun(runId)", "\nfunction startContentOSPolling") +
    section("async function cancelContentOSRun(runId)", '\ndocument.querySelectorAll("#publish-modal [data-platform]")'), context);
  context.renderContentOSRuns();
  const handlers = [...elements["#content-os-runs-list"].innerHTML.matchAll(/onclick="([^"]+)"/g)]
    .map(match => match[1]).filter(handler => handler.includes("event.stopPropagation()"));
  assert.equal(handlers.length, 4);
  for (const handler of handlers) await vm.runInContext(handler, context);
  assert.deepEqual(calls.map(([url]) => url), [
    "/api/content-os/runs/1/start", "/api/content-os/runs/2/cancel",
    "/api/content-os/runs/3/approve", "/api/content-os/runs/4/create-job",
  ]);
  assert.ok(calls.every(([, opts]) => opts.method === "POST"));
  assert.deepEqual(JSON.parse(calls[2][1].body), {approval_type: "script", decision: "approved", note: ""});
  assert.deepEqual(JSON.parse(calls[3][1].body), {source_url: null});
  assert.deepEqual(selections, [1, 2, 3]);
  calls.length = 0;
  for (const id of ["#content-os-start-run-btn", "#content-os-cancel-run-btn",
    "#content-os-approve-run-btn", "#content-os-create-job-btn"]) {
    await elements[id].onclick();
  }
  assert.ok(calls.every(([url]) => url.includes("/runs/99/")));
  calls.length = 0;
  context.confirm = () => false;
  await context.cancelContentOSRun(2);
  assert.equal(calls.length, 0);
});

function confirmationSetup(api, userId = 17) {
  let refreshes = 0;
  const ui = setup({
    api, currentUserId: userId,
    PLATFORM_LABEL: {youtube: "YouTube", facebook: "Facebook Page", tiktok: "TikTok"},
    location: new URL("https://app.test/?keep=yes&social_confirmation=opaque%2Ftoken#accounts"),
    clearInterval() {}, loadConnections: async () => { refreshes++; },
  });
  ui.context.history = {state: {keep: true}, replaceState(state, unused, url) {
    ui.context.location = new URL(url, ui.context.location);
  }};
  vm.runInContext(section("let socialConnectionRequest", "\nasync function loadConnections") +
    section("function closeSocialConnect()", "\nasync function checkSocialConnection"), ui.context);
  return {...ui, refreshes: () => refreshes};
}

test("social confirmation waits for authentication and an explicit click; success cleans only its query", async () => {
  const calls = [];
  let finishPost;
  const {context, elements, refreshes} = confirmationSetup(async (url, opts) => {
    calls.push([url, opts?.method || "GET"]);
    if (opts) return new Promise(resolve => { finishPost = resolve; });
    return {platform: "youtube", account_name: '<Team "One">'};
  }, null);
  await context.handleSocialConfirmation();
  assert.equal(calls.length, 0);
  context.currentUserId = 17;
  await context.handleSocialConfirmation();
  await context.handleSocialConfirmation();
  assert.deepEqual(calls, [["/api/social/confirm/opaque%2Ftoken", "GET"]]);
  const markup = elements["#social-connect-content"].innerHTML;
  assert.match(markup, /YouTube/);
  assert.match(markup, /&lt;Team &quot;One&quot;&gt;/);
  assert.equal(refreshes(), 0);
  const button = elements["#social-confirm-submit"];
  const pending = button.onclick();
  await button.onclick();
  assert.deepEqual(calls.map(([, method]) => method), ["GET", "POST"]);
  finishPost({ok: true});
  await pending;
  assert.equal(refreshes(), 1);
  assert.equal(context.location.href, "https://app.test/?keep=yes#accounts");
  assert.ok(elements["#social-connect-modal"].classList.contains("hidden"));
});

test("confirmation lookup failures show a generic message without activating or exposing errors", async () => {
  const calls = [];
  const {context, elements, refreshes} = confirmationSetup(async (url, opts) => {
    calls.push(opts?.method || "GET");
    throw new Error("internal provider credential sentinel");
  });
  await context.handleSocialConfirmation();
  assert.deepEqual(calls, ["GET"]);
  assert.match(elements["#social-connect-content"].textContent, /Không thể xác nhận/);
  assert.doesNotMatch(elements["#social-connect-content"].textContent, /sentinel/);
  assert.equal(context.location.search, "?keep=yes");
  assert.equal(refreshes(), 0);
});

test("closing confirmation ignores a late lookup and never activates the account", async () => {
  let finish;
  const calls = [];
  const {context, elements} = confirmationSetup((url, opts) => {
    calls.push(opts?.method || "GET");
    return new Promise(resolve => { finish = resolve; });
  });
  const pending = context.handleSocialConfirmation();
  context.closeSocialConnect();
  finish({platform: "youtube", account_name: "Late account"});
  await pending;
  assert.deepEqual(calls, ["GET"]);
  assert.equal(elements["#social-connect-content"].innerHTML, "");
  assert.ok(elements["#social-connect-modal"].classList.contains("hidden"));
  assert.equal(context.location.search, "?keep=yes");
});

test("failed activation retains confirmation for retry and hides server details", async () => {
  let posts = 0;
  const {context, elements, refreshes} = confirmationSetup(async (url, opts) => {
    if (!opts) return {platform: "youtube", account_name: "My channel"};
    if (++posts === 1) throw new Error("internal credential sentinel");
    return {ok: true};
  });
  await context.handleSocialConfirmation();
  const button = elements["#social-confirm-submit"];
  await button.onclick();
  assert.equal(button.disabled, false);
  assert.doesNotMatch(elements["#social-confirm-error"].textContent, /sentinel/);
  assert.ok(context.location.search.includes("social_confirmation"));
  assert.equal(refreshes(), 0);
  await button.onclick();
  assert.equal(posts, 2);
  assert.equal(refreshes(), 1);
});

function qrSetup(api) {
  const ui = confirmationSetup(api);
  const timers = new Map();
  let now = 0;
  ui.context.location = new URL("https://app.test/?keep=yes");
  ui.context.window.open = () => null;
  ui.context.Date = class extends Date { static now() { return now; } };
  ui.context.setInterval = callback => { const id = Symbol(); timers.set(id, callback); return id; };
  ui.context.clearInterval = id => timers.delete(id);
  vm.runInContext(section("async function checkSocialConnection()", '\n$("#social-refresh-btn")') +
    section("async function startConnect(platform)", '\n$("#publish-submit")'), ui.context);
  return {...ui, timers, expire: () => { now = 10 * 60 * 1000; }};
}

test("original QR window polls pending confirmations and waits for a click after another device authorizes", async () => {
  let authorized = false;
  const calls = [];
  const {context, elements, refreshes, timers} = qrSetup(async (url, opts) => {
    calls.push([url, opts?.method || "GET"]);
    if (url.includes("/connect/")) return {
      authorize_url: "https://provider.test/consent", qr_code_url: "/qr", confirmation_token: "qr/token",
    };
    if (!opts && !authorized) throw Object.assign(new Error("Pending"), {status: 404});
    return opts ? {ok: true} : {platform: "youtube", account_name: 'QR "Owner"'};
  });
  await context.startConnect("youtube");
  assert.match(elements["#social-connect-content"].innerHTML, /connect-qr-box/);
  await context.checkSocialConnection();
  assert.equal(timers.size, 1);
  assert.equal(elements["#social-confirm-submit"], undefined);
  assert.equal(refreshes(), 0);
  authorized = true;
  await context.checkSocialConnection();
  assert.equal(timers.size, 0);
  assert.match(elements["#social-connect-content"].innerHTML, /QR &quot;Owner&quot;/);
  assert.ok(calls.every(([, method]) => method === "GET"));
  assert.deepEqual(calls.map(([url]) => url), [
    "/api/social/connect/youtube", "/api/social/confirm/qr%2Ftoken", "/api/social/confirm/qr%2Ftoken",
  ]);
  const button = elements["#social-confirm-submit"];
  await button.onclick();
  assert.deepEqual(calls.at(-1), ["/api/social/confirm/qr%2Ftoken", "POST"]);
  assert.equal(refreshes(), 1);
  assert.equal(context.location.search, "?keep=yes");
  await button.onclick();
  assert.equal(calls.filter(([, method]) => method === "POST").length, 1);
});

test("QR polling stops after ten minutes and ignores an in-flight result", async () => {
  let finish;
  const calls = [];
  const {context, elements, timers, expire} = qrSetup(async (url, opts) => {
    calls.push(opts?.method || "GET");
    if (url.includes("/connect/")) return {authorize_url: "/consent", qr_code_url: "/qr", confirmation_token: "pending"};
    return new Promise(resolve => { finish = resolve; });
  });
  await context.startConnect("youtube");
  const pending = context.checkSocialConnection();
  expire();
  [...timers.values()][0]();
  assert.equal(timers.size, 0);
  finish({platform: "youtube", account_name: "Too late"});
  await pending;
  assert.match(elements["#social-connect-content"].textContent, /Thời gian chờ/);
  assert.equal(elements["#social-confirm-submit"], undefined);
  assert.ok(calls.every(method => method === "GET"));
});

test("confirmed popup notifies only its same-origin opener with its confirmation token", async () => {
  const messages = [];
  const {context, elements} = confirmationSetup(async () => ({platform: "youtube", account_name: "Owner"}));
  context.window.opener = {postMessage: (data, origin) => messages.push([data.type, data.token, origin])};
  await context.handleSocialConfirmation();
  await elements["#social-confirm-submit"].onclick();
  assert.deepEqual(messages, [["social-confirmed", "opaque/token", "https://app.test"]]);
});

test("API errors retain HTTP status so pending QR 404 responses can be retried", async () => {
  const {context} = setup({fetch: async () => ({ok: false, status: 404, json: async () => ({detail: "Pending"})})});
  vm.runInContext(section("async function api(path", "\n// ---------------- auth"), context);
  await assert.rejects(context.api("/api/social/confirm/pending"), error => error.status === 404);
});

test("successful authentication opens confirmation; unauthenticated callbacks open login", async () => {
  let promptedUser = null, loginShown = false;
  const {context} = setup({
    currentUserId: null, location: new URL("https://app.test/?social_confirmation=pending"),
    loadLanguages: async () => {}, restoreLocalizationDraft: () => true, updateJobEstimate() {},
    loadProviderSettings() {}, loadPublishingProfiles() {}, loadConnections() {},
    loadPersonalStats() {}, refreshJobQueueStatus() {}, requestNotificationPermission() {}, refreshJobs() {}, refreshMe() {},
    handleSocialConfirmation() { promptedUser = context.currentUserId; }, setInterval() {},
    bootstrapConfig: {}, showLanding() {}, showAuthTab: tab => { assert.equal(tab, "login"); },
    showAuth: () => { loginShown = true; },
    api: async url => {
      if (url === "/api/bootstrap") return {features: {}};
      throw new Error("unauthenticated");
    },
  });
  context.document.querySelector = context.$;
  vm.runInContext(section("async function initAuth()", "\nfunction showLanding") +
    section("function showApp(me)", '\n$("#referral-copy-btn")'), context);
  await context.initAuth();
  assert.equal(loginShown, true);
  assert.equal(promptedUser, null);
  context.showApp({id: 17, username: "Owner", credits: 0});
  assert.equal(promptedUser, 17);
});
