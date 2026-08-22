// Inbox list view: stamp an importance badge (+ topic chip) on each row.
//
// Gmail row anatomy: each list row is a <tr class="zA"> containing a
// <span data-legacy-thread-id="..."> (hex id matching the Gmail API's
// threadId, i.e. our ProcessedEmail.thread_id). The DOM is otherwise
// obfuscated and unstable — data-legacy-thread-id is the one durable hook.

(() => {
  const WRAP_CLASS = "ea-row-chips";

  function chipsFor(email) {
    const wrap = document.createElement("span");
    wrap.className = WRAP_CLASS;
    // Fingerprint of what's rendered, so unchanged rows are skipped — the
    // MutationObserver sees our own insertions, and re-rendering every tick
    // would loop forever.
    wrap.dataset.eaKey = fingerprint(email);

    const level = email.importanceLevel || "unscored";
    const badge = document.createElement("span");
    badge.className = `ea-badge ea-level-${level}`;
    badge.textContent =
      email.importanceScore != null ? `${level} ${Math.round(email.importanceScore)}` : level;
    if (email.importanceJustification) badge.title = email.importanceJustification;
    wrap.appendChild(badge);

    if (email.category) {
      const topic = document.createElement("span");
      topic.className = "ea-chip ea-row-topic";
      topic.textContent = email.category;
      wrap.appendChild(topic);
    }
    if (email.isSchedulingRelated) {
      const cal = document.createElement("span");
      cal.className = "ea-chip ea-row-topic";
      cal.textContent = "📅";
      cal.title = "Scheduling-related — calendar checked";
      wrap.appendChild(cal);
    }
    return wrap;
  }

  function fingerprint(email) {
    return [email.importanceLevel, email.importanceScore, email.category, email.isSchedulingRelated].join("|");
  }

  function decorateRow(threadSpan) {
    const threadId = threadSpan.getAttribute("data-legacy-thread-id");
    const email = EmailAgent.forThread(threadId);
    const parent = threadSpan.parentElement;
    if (!parent) return;
    const existing = parent.querySelector(`.${WRAP_CLASS}`);

    if (!email || !email.importanceLevel) {
      if (existing) existing.remove();
      return;
    }
    if (existing) {
      if (existing.dataset.eaKey === fingerprint(email)) return; // unchanged
      existing.replaceWith(chipsFor(email));
      return;
    }
    parent.insertBefore(chipsFor(email), threadSpan);
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
