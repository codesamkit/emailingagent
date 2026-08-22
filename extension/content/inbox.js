// Inbox list view: stamp an importance badge on each thread row.
//
// Gmail row anatomy: each list row is a <tr class="zA"> containing a
// <span data-legacy-thread-id="..."> (hex id matching the Gmail API's
// threadId, i.e. our ProcessedEmail.thread_id). The DOM is otherwise
// obfuscated and unstable — data-legacy-thread-id is the one durable hook.

(() => {
  const BADGE_CLASS = "ea-badge";

  function badgeFor(email) {
    const badge = document.createElement("span");
    const level = email.importanceLevel || "unscored";
    badge.className = `${BADGE_CLASS} ea-level-${level}`;
    badge.textContent =
      email.importanceScore != null ? `${level} ${Math.round(email.importanceScore)}` : level;
    if (email.importanceJustification) badge.title = email.importanceJustification;
    return badge;
  }

  function decorateRow(threadSpan) {
    const threadId = threadSpan.getAttribute("data-legacy-thread-id");
    const email = EmailAgent.forThread(threadId);
    const existing = threadSpan.parentElement?.querySelector(`.${BADGE_CLASS}`);

    if (!email || !email.importanceLevel) {
      if (existing) existing.remove();
      return;
    }
    if (existing) {
      // Re-render in place so score changes after a pipeline re-run show up.
      existing.replaceWith(badgeFor(email));
      return;
    }
    threadSpan.parentElement?.insertBefore(badgeFor(email), threadSpan);
  }

  function decorateAll() {
    document.querySelectorAll("span[data-legacy-thread-id]").forEach(decorateRow);
  }

  let scheduled = null;
  function scheduleDecorate() {
    if (scheduled) return;
    scheduled = setTimeout(() => {
      scheduled = null;
      decorateAll();
    }, 250);
  }

  // Gmail is a SPA: rows appear/disappear on scroll, archive, label switch.
  new MutationObserver(scheduleDecorate).observe(document.body, {
    childList: true,
    subtree: true,
  });
  window.addEventListener("hashchange", scheduleDecorate);
  EmailAgent.onRefresh(scheduleDecorate);
  scheduleDecorate();
})();
