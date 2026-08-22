// Shared API client + processed-email index for the Gmail content scripts.
// Loaded first (see manifest content_scripts order); inbox.js and detail.js
// use the `EmailAgent` global it defines.

const EmailAgent = (() => {
  const REFRESH_INTERVAL_MS = 60_000;

  // threadId -> email summary row, emailId -> email summary row
  const byThread = new Map();
  const byEmailId = new Map();
  let lastRefresh = 0;
  let backendReachable = null; // null = unknown, then true/false
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

  async function ensureFresh() {
    if (Date.now() - lastRefresh > REFRESH_INTERVAL_MS) await refreshIndex();
  }

  refreshIndex();
  setInterval(refreshIndex, REFRESH_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ensureFresh();
  });

  return {
    call,
    refreshIndex,
    onRefresh: (fn) => refreshListeners.push(fn),
    forThread: (threadId) => byThread.get(threadId),
    forEmail: (emailId) => byEmailId.get(emailId),
    isReachable: () => backendReachable,
    getDetail: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}`),
    expandDraft: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}/expand`, "POST"),
  };
})();
