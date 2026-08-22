// Shared API client + processed-email index for the Gmail content scripts.
// Loaded first (see manifest content_scripts order); inbox.js and detail.js
// use the `EmailAgent` global it defines.

const EmailAgent = (() => {
  const INDEX_REFRESH_MS = 60_000; // re-read the processed-email index
  const PIPELINE_REFRESH_MS = 120_000; // ingest + process new mail via the backend

  // threadId -> email summary row, emailId -> email summary row
  const byThread = new Map();
  const byEmailId = new Map();
  let lastRefresh = 0;
  let backendReachable = null; // null = unknown, then true/false
  let pipelineRunning = false;
  const refreshListeners = [];

  function call(path, method = "GET", body = null) {
    return chrome.runtime.sendMessage({ type: "api", path, method, body });
  }

  async function refreshIndex() {
    const result = await call("/api/emails?limit=1000");
    backendReachable = !!result?.ok;
    if (!result?.ok) return false;

    byThread.clear();
    byEmailId.clear();
    for (const email of result.data.emails || []) {
      byEmailId.set(email.emailId, email);
      // Keep the most important email as the thread's representative so the
      // inbox badge reflects the strongest signal in the thread.
      const existing = byThread.get(email.threadId);
      if (!existing || (email.importanceScore ?? -1) > (existing.importanceScore ?? -1)) {
        byThread.set(email.threadId, email);
      }
    }
    lastRefresh = Date.now();
    refreshListeners.forEach((fn) => fn());
    return true;
  }

  // Ask the backend to ingest new mail + process what changed, then re-read
  // the index. A 409 means another caller's refresh is mid-flight — its
  // results land in the same database, so just re-read the index after a beat.
  async function refreshPipeline() {
    if (pipelineRunning) return { ok: false, status: 0, busy: true };
    pipelineRunning = true;
    try {
      const result = await call("/api/refresh", "POST");
      if (result?.status === 409) {
        await new Promise((r) => setTimeout(r, 10_000));
      }
      await refreshIndex();
      return result;
    } finally {
      pipelineRunning = false;
    }
  }

  async function ensureFresh() {
    if (Date.now() - lastRefresh > INDEX_REFRESH_MS) await refreshIndex();
  }

  refreshIndex().then(() => refreshPipeline());
  setInterval(refreshIndex, INDEX_REFRESH_MS);
  setInterval(refreshPipeline, PIPELINE_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ensureFresh();
  });

  return {
    call,
    refreshIndex,
    refreshPipeline,
    onRefresh: (fn) => refreshListeners.push(fn),
    forThread: (threadId) => byThread.get(threadId),
    forEmail: (emailId) => byEmailId.get(emailId),
    allEmails: () => [...byEmailId.values()],
    isReachable: () => backendReachable,
    getDetail: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}`),
    // Fast path: re-fetch one message (picks up read flips / brand-new mail)
    // and run only the stages it needs — seconds, vs minutes for /api/refresh.
    refreshEmail: async (emailId) => {
      const result = await call(`/api/emails/${encodeURIComponent(emailId)}/refresh`, "POST");
      if (result?.ok) await refreshIndex();
      return result;
    },
    expandDraft: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}/expand`, "POST"),
    // Human-approval flow for proposed calendar events. Approve is the one
    // call that writes to Google Calendar — only ever fired by a user click.
    approveEvent: (emailId) =>
      call(`/api/emails/${encodeURIComponent(emailId)}/calendar-event/approve`, "POST"),
    declineEvent: (emailId) =>
      call(`/api/emails/${encodeURIComponent(emailId)}/calendar-event/decline`, "POST"),
  };
})();
