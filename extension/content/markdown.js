// Minimal Markdown -> DOM renderer for model output, exposed as
// EmailAgentMarkdown.render(text, target).
//
// Builds real nodes and puts every scrap of model text through textContent,
// never innerHTML — the same rule the rest of content/ follows, and it matters
// more here than anywhere else: this text is synthesized from email bodies, so
// it is attacker-influenced content being drawn inside the user's mail client.
// A markdown-to-HTML-string library plus innerHTML would hand that content an
// injection surface; producing nodes directly means markup in an email can only
// ever come out as literal characters.
//
// Deliberately covers just what the agent emits (agent/loop.py's SYSTEM_PROMPT
// yields headings, bullet/numbered lists, bold, and inline code) rather than
// being a general CommonMark implementation. Anything unrecognized falls
// through as plain text, which is the same thing the user saw before.

const EmailAgentMarkdown = (() => {
  const HEADING = /^(#{1,6})\s+(.*)$/;
  const BULLET = /^\s*[-*+]\s+(.*)$/;
  const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
  // Inline runs, longest-delimiter-first so ** wins over *.
  const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/;

  function inline(text, parent) {
    // Split on the delimiter alternation; odd indices are the matched runs
    // because INLINE is a single capturing group.
    const parts = String(text).split(INLINE);
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i];
      if (!part) continue;

      if (i % 2 === 0) {
        parent.appendChild(document.createTextNode(part));
        continue;
      }

      let tag = "em";
      let inner = part.slice(1, -1);
      if (part.startsWith("**") || part.startsWith("__")) {
        tag = "strong";
        inner = part.slice(2, -2);
      } else if (part.startsWith("`")) {
        tag = "code";
      }

      const node = document.createElement(tag);
      node.textContent = inner; // never innerHTML
      parent.appendChild(node);
    }
  }

  function flushParagraph(lines, target) {
    if (!lines.length) return;
    const p = document.createElement("p");
    p.className = "ea-md-p";
    inline(lines.join(" "), p);
    target.appendChild(p);
    lines.length = 0;
  }

  function flushList(items, ordered, target) {
    if (!items.length) return;
    const list = document.createElement(ordered ? "ol" : "ul");
    list.className = "ea-md-list";
    for (const item of items) {
      const li = document.createElement("li");
      inline(item, li);
      list.appendChild(li);
    }
    target.appendChild(list);
    items.length = 0;
  }

  function render(text, target) {
    target.textContent = "";
    if (!text) return target;

    const lines = String(text).split(/\r?\n/);
    const paragraph = [];
    let items = [];
    let ordered = false;

    const flushAll = () => {
      flushParagraph(paragraph, target);
      flushList(items, ordered, target);
    };

    for (const line of lines) {
      if (!line.trim()) {
        flushAll();
        continue;
      }

      const heading = HEADING.exec(line);
      if (heading) {
        flushAll();
        // Cap at h6, and offset so an agent's "##" doesn't outrank the panel's
        // own chrome in the surrounding page.
        const level = Math.min(6, heading[1].length + 2);
        const node = document.createElement(`h${level}`);
        node.className = "ea-md-h";
        inline(heading[2], node);
        target.appendChild(node);
        continue;
      }

      const bullet = BULLET.exec(line);
      const numbered = ORDERED.exec(line);
      if (bullet || numbered) {
        flushParagraph(paragraph, target);
        const isOrdered = Boolean(numbered);
        // A switch between list styles starts a new list.
        if (items.length && isOrdered !== ordered) flushList(items, ordered, target);
        ordered = isOrdered;
        items.push(numbered ? numbered[2] : bullet[1]);
        continue;
      }

      flushList(items, ordered, target);
      paragraph.push(line.trim());
    }

    flushAll();
    return target;
  }

  return { render };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = { EmailAgentMarkdown };
}
