"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../src/universal_video_ai/web/static/app.js"), "utf8");
const block = source.slice(source.indexOf("function updateHistorySelection("), source.indexOf('$("#history-select-all").onchange'));

function setup(jobs, api) {
  const elements = {};
  const alerts = [];
  const context = {
    window: {_jobsById: Object.fromEntries(jobs.map(job => [job.id, job]))},
    selectedHistoryJobs: new Set(jobs.map(job => job.id)), historyBulkRerunBusy: false,
    $: selector => elements[selector] ||= {},
    showConfirmDialog: async () => true,
    refreshJobs: async () => {}, refreshMe: async () => {},
    alert: message => alerts.push(message), api,
  };
  vm.createContext(context);
  vm.runInContext(block, context);
  return {context, elements, alerts};
}

test("selection runs only checked stopped items, retains failures and clears queued selections", async () => {
  const jobs = [
    {id: "a", status: "cancelled"}, {id: "b", status: "error"}, {id: "c", status: "done"},
    {id: "d", status: "running"}, {id: "e", status: "error", is_content_os: true},
    {id: "f", status: "cancelled"},
  ];
  const calls = [];
  const {context, elements, alerts} = setup(jobs, async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    return {queued: ["a"], skipped: [], errors: [{job_id: "b", reason: "stopping"}]};
  });
  context.selectedHistoryJobs.delete("f");
  context.updateHistorySelection(jobs);
  assert.equal(elements["#history-retry-selected"].textContent, "Thử lại đã chọn (2)");
  await context.rerunSelectedHistoryJobs();
  assert.deepEqual(calls, [["/api/jobs/bulk-rerun", {job_ids: ["a", "b"]}]]);
  assert.equal(context.selectedHistoryJobs.has("a"), false);
  assert.equal(context.selectedHistoryJobs.has("b"), true);
  assert.match(alerts[0], /stopping/);
  assert.equal(context.historyBulkRerunBusy, false);
});

test("double click while confirming creates only one batch request", async () => {
  let confirm;
  let calls = 0;
  const {context} = setup([{id: "a", status: "cancelled"}], async () => {
    calls++;
    return {queued: ["a"]};
  });
  context.showConfirmDialog = () => new Promise(resolve => { confirm = resolve; });
  const first = context.rerunSelectedHistoryJobs();
  await context.rerunSelectedHistoryJobs();
  assert.equal(calls, 0);
  confirm(true);
  await first;
  assert.equal(calls, 1);
});

test("cancelled confirmation and request failure retain the selection", async () => {
  const {context} = setup([{id: "a", status: "cancelled"}], async () => {
    throw new Error("offline");
  });
  context.showConfirmDialog = async () => false;
  await context.rerunSelectedHistoryJobs();
  assert.equal(context.selectedHistoryJobs.has("a"), true);
  context.showConfirmDialog = async () => true;
  await context.rerunSelectedHistoryJobs();
  assert.equal(context.selectedHistoryJobs.has("a"), true);
  assert.equal(context.historyBulkRerunBusy, false);
});

test("large selections enqueue in bounded batches without per-video polling", async () => {
  const calls = [];
  const jobs = Array.from({length: 205}, (_, i) => ({id: String(i), status: "cancelled"}));
  const {context} = setup(jobs, async (_, options) => {
    const ids = JSON.parse(options.body).job_ids;
    calls.push(ids.length);
    return {queued: ids};
  });
  await context.rerunSelectedHistoryJobs();
  assert.deepEqual(calls, [100, 100, 5]);
  assert.equal(context.selectedHistoryJobs.size, 0);
});

test("retry all works with an empty filtered page and makes one global request", async () => {
  const calls = [];
  const {context, elements} = setup([], async url => { calls.push(url); return {queued: ["off-page"]}; });
  context.updateHistorySelection([]);
  assert.equal(elements["#history-retry-all"].disabled, false);
  await context.rerunSelectedHistoryJobs(true);
  assert.deepEqual(calls, ["/api/jobs/retry-all-incomplete"]);
});

test("a single local calendar date covers its entire last second", () => {
  const dateBlock = source.slice(source.indexOf("function _dateToUnix("), source.indexOf("function updateStats("));
  const ctx = vm.createContext({Date});
  vm.runInContext(dateBlock, ctx);
  const start = ctx._dateToUnix("2026-09-08", false);
  const end = ctx._dateToUnix("2026-09-08", true);
  assert.equal(new Date(start * 1000).getHours(), 0);
  assert.equal(new Date(end * 1000).getMilliseconds(), 999);
  assert.equal(new Date(end * 1000).getDate(), 8);
  assert.equal(ctx._dateToUnix("", false), null);
  assert.equal(source.includes('$("#history-date-from")'), false);
  assert.equal(source.includes('$("#history-date-to")'), false);
});
