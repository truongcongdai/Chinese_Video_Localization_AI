"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8");
const social = source.slice(source.lastIndexOf('document.querySelectorAll("#publish-modal [data-platform]").forEach(chip => {'), source.indexOf("// ---------------- admin ----------------"));
const openPublish = source.slice(source.indexOf("function openPublish(jobId)"), source.indexOf('$("#publish-close").onclick'));
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function element(dataset = {}) {
  const classes = new Set();
  const children = {};
  return {
    dataset, innerHTML: "", textContent: "", value: "", disabled: false,
    classList: {
      add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle: (c, on) => on ? classes.add(c) : classes.delete(c),
    },
    setAttribute(name, value) { this[name] = value; },
    addEventListener() {},
    querySelector: selector => children[selector] ||= element(),
    querySelectorAll: () => [],
  };
}

function setup(api) {
  const elements = {};
  const boxes = [element(), element(), element()];
  const chips = [element({platform: "youtube"}), element({platform: "facebook"}), element({platform: "tiktok"})];
  const timers = new Map();
  const context = {
    $: selector => elements[selector] ||= element(), escapeHtml,
    document: {querySelectorAll: selector => selector === "[data-social-connections]" ? boxes : chips, addEventListener() {}, dispatchEvent() {}},
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: {_jobsById: {a: {title: "Video A"}, b: {title: "Video B"}}, open: () => null},
    selectedPlatforms: new Set(), currentJobId: "preview", publishJobId: null, latestResultJobId: "a",
    api, alert() {}, uiLocale: () => "vi-VN", _prefillPublishSuggestions() {},
    setInterval: callback => { const id = Symbol(); timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id),
  };
  vm.createContext(context);
  vm.runInContext(social + openPublish, context);
  return {context, elements, boxes, chips, timers};
}

const connections = {
  youtube: {configured: true, connected: true, account_name: '<Channel & "Team">'},
  facebook: {configured: true, connected: false},
  tiktok: {configured: false, connected: false, not_configured_message: "Thiếu TIKTOK_CLIENT_KEY"},
};

test("all connection surfaces share account state and escape names", async () => {
  const {context, boxes} = setup(async () => connections);
  await context.loadConnections();
  assert.equal(new Set(boxes.map(box => box.innerHTML)).size, 1);
  assert.match(boxes[0].innerHTML, /&lt;Channel &amp; &quot;Team&quot;&gt;/);
  assert.match(boxes[0].innerHTML, /data-connect="tiktok" disabled/);
  assert.match(boxes[0].innerHTML, /Thiếu TIKTOK_CLIENT_KEY/);
});

test("connection errors remain visible and a late response cannot overwrite newer state", async () => {
  let resolve;
  let count = 0;
  const {context, boxes} = setup(() => ++count === 1 ? new Promise(r => { resolve = r; }) : Promise.resolve(connections));
  const old = context.loadConnections();
  await context.loadConnections();
  resolve({});
  await old;
  assert.match(boxes[0].innerHTML, /Channel/);
  context.api = async () => { throw new Error("offline <error>"); };
  await context.loadConnections();
  assert.match(boxes[0].innerHTML, /offline &lt;error&gt;/);
  assert.equal(typeof boxes[0].querySelector("[data-retry-connections]").onclick, "function");
});

test("OAuth opens synchronously, then refreshes every surface and stops polling", async () => {
  const events = [];
  const {context, elements, timers, boxes} = setup(async url => {
    events.push(url);
    return url.includes("/connect/") ? {authorize_url: "https://provider.test/consent", qr_code_url: "https://qr.test/image"} : connections;
  });
  const popup = {closed: false, location: {}, close() { this.closed = true; }};
  context.window.open = () => { events.push("popup"); return popup; };
  await context.startConnect("youtube");
  assert.equal(events[0], "popup");
  assert.equal(popup.location.href, "https://provider.test/consent");
  assert.match(elements["#social-connect-content"].innerHTML, /qr.test/);
  assert.equal(timers.size, 1);
  await context.checkSocialConnection();
  assert.equal(timers.size, 0);
  assert.equal(elements["#social-connect-modal"].classList.contains("hidden"), true);
  assert.match(boxes[2].innerHTML, /Channel/);
});

test("blocked popups provide a login link; configuration failures remain readable", async () => {
  const {context, elements, timers} = setup(async () => ({authorize_url: "https://provider.test/consent?a=1&b=2", qr_code_url: "/qr"}));
  await context.startConnect("facebook");
  assert.match(elements["#social-connect-content"].innerHTML, /href="https:\/\/provider.test\/consent\?a=1&amp;b=2"/);
  context.api = async () => { throw new Error("Thiếu FACEBOOK_APP_ID"); };
  await context.startConnect("facebook");
  assert.equal(elements["#social-connect-content"].textContent, "Thiếu FACEBOOK_APP_ID");
  assert.equal(timers.size, 0);
});

test("closing OAuth while its request is pending prevents stale content and timers", async () => {
  let resolve;
  const {context, elements, timers} = setup(() => new Promise(r => { resolve = r; }));
  const pending = context.startConnect("youtube");
  context.closeSocialConnect();
  resolve({authorize_url: "https://provider.test", qr_code_url: "/qr"});
  await pending;
  assert.equal(elements["#social-connect-modal"].classList.contains("hidden"), true);
  assert.equal(timers.size, 0);
});

test("ordinary-video publishing clears old metadata and stays independent of the preview job", async () => {
  const calls = [];
  const {context, elements, chips} = setup(async (url, options) => {
    if (options) calls.push([url, JSON.parse(options.body)]);
    if (url.endsWith("/publish")) return {results: [{platform: "youtube", success: true, message: "Uploaded"}]};
    return url === "/api/scheduled-posts" ? [] : connections;
  });
  context.openPublish("a");
  chips[0].onclick();
  elements["#publish-desc"].value = "Description A";
  context.currentJobId = "another-preview";
  await elements["#publish-submit"].onclick();
  assert.equal(calls[0][0], "/api/jobs/a/publish");
  assert.equal(calls[0][1].description, "Description A");
  context.openPublish("b");
  assert.equal(elements["#publish-title"].value, "Video B");
  assert.equal(elements["#publish-desc"].value, "");
  assert.equal(chips[0]["aria-pressed"], "false");
  assert.equal(context.selectedPlatforms.size, 0);
  chips[1].onclick();
  elements["#publish-scheduled-at"].value = "2027-01-01T12:00";
  await elements["#publish-schedule"].onclick();
  assert.equal(calls[1][0], "/api/jobs/b/schedule-publish");
  assert.deepEqual(calls[1][1].platforms, ["facebook"]);
});

test("changing accounts waits for a new connection revision", async () => {
  let revision = 1;
  const {context, elements} = setup(async url => url.includes("/connect/")
    ? {authorize_url: "https://provider.test/consent", qr_code_url: "/qr"}
    : {youtube: {configured: true, connected: true, updated_at: revision}});
  await context.loadConnections();
  await context.startConnect("youtube");
  await context.checkSocialConnection();
  assert.equal(elements["#social-connect-modal"].classList.contains("hidden"), false);
  revision = 2;
  await context.checkSocialConnection();
  assert.equal(elements["#social-connect-modal"].classList.contains("hidden"), true);
});

test("admin app setup submits credentials through the API and clears the secret input", async () => {
  const submitted = [];
  const app = {platform: "tiktok", client_id: "", id_label: "Client key", redirect_uri: "https://app.test/callback", dashboard_url: "https://provider.test/apps", configured: false};
  const {context, elements} = setup(async (url, options) => {
    if (url === "/api/admin/social-apps") return {apps: [app]};
    if (options) {
      submitted.push([url, JSON.parse(options.body)]);
      return {...app, client_id: "ui-key", configured: true};
    }
    return connections;
  });
  context.$("#social-app-platform").value = "tiktok";
  await context.loadSocialAppSettings();
  elements["#social-app-id"].value = "ui-key";
  elements["#social-app-secret"].value = "ui-secret";
  await elements["#social-app-form"].onsubmit({preventDefault() {}});
  assert.deepEqual(submitted, [["/api/admin/social-apps/tiktok", {client_id: "ui-key", client_secret: "ui-secret"}]]);
  assert.equal(elements["#social-app-secret"].value, "");
  assert.equal(elements["#social-app-save"].disabled, false);
});
