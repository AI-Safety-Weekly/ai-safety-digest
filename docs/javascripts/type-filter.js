/* Content-type filter for AI Safety Digest pages.
 *
 * Every digest entry is tagged with a content-type badge (Paper / Blog post /
 * Other). This injects a small toolbar at the top of each digest page that
 * lets the reader show only the types they care about. Pure client-side: it
 * shows/hides already-rendered entries, persisting the choice across pages in
 * sessionStorage.
 *
 * Entries come in two shapes:
 *   - full entries: an <h3> whose first child is a `.type-badge`, followed by
 *     its summary/meta/<details> siblings up to the next heading;
 *   - Zone 3 one-liners: an <li> inside the "Browse all" <details> that starts
 *     with a `.type-badge`.
 * Section chrome (the <h2>, its intro blurb, the brief) is hidden when a filter
 * empties the whole section, so the page never shows a dangling header.
 */

const FILTER_KEY = "digest-type-filter";

const FILTERS = [
  { value: "all", label: "All" },
  { value: "paper", label: "Papers" },
  { value: "blog_post", label: "Blog posts" },
  { value: "other", label: "Other" },
];

function typeOf(node) {
  const badge = node.querySelector(".type-badge");
  if (!badge) return null;
  if (badge.classList.contains("type-badge-paper")) return "paper";
  if (badge.classList.contains("type-badge-blog-post")) return "blog_post";
  if (badge.classList.contains("type-badge-other")) return "other";
  return null;
}

/* Group the flat sibling stream under .md-typeset into sections (one per <h2>),
 * recording each section's filterable entries and the toggle target(s) for
 * each. Returns [{ chrome: [nodes], entries: [{type, nodes:[...]}] }]. */
function buildSections(root) {
  const sections = [];
  let cur = null;
  for (const node of Array.from(root.children)) {
    if (node.tagName === "H2") {
      cur = { nodes: [node], entries: [], entryNodes: new Set() };
      sections.push(cur);
    } else if (cur) {
      cur.nodes.push(node);
    }
  }

  for (const section of sections) {
    const ns = section.nodes;
    for (let i = 1; i < ns.length; i++) {
      const node = ns[i];
      if (node.tagName === "H3") {
        const t = typeOf(node);
        const group = [node];
        let j = i + 1;
        for (; j < ns.length && ns[j].tagName !== "H3"; j++) group.push(ns[j]);
        if (t) {
          section.entries.push({ type: t, nodes: group });
          group.forEach((n) => section.entryNodes.add(n));
        }
        i = j - 1;
      } else if (node.querySelectorAll) {
        // Zone 3 one-liners live inside a nested <details>; toggle each <li>.
        node.querySelectorAll("li").forEach((li) => {
          const t = typeOf(li);
          if (t) section.entries.push({ type: t, nodes: [li] });
        });
      }
    }
  }
  return sections;
}

function applyFilter(sections, filter) {
  for (const section of sections) {
    let anyVisible = false;
    for (const entry of section.entries) {
      const show = filter === "all" || entry.type === filter;
      entry.nodes.forEach((n) => (n.style.display = show ? "" : "none"));
      if (show) anyVisible = true;
    }
    // Hide section chrome (header, intro, brief, the <details> wrapper) only
    // when the section had entries and the filter emptied all of them.
    if (section.entries.length) {
      section.nodes.forEach((n) => {
        if (!section.entryNodes.has(n)) n.style.display = anyVisible ? "" : "none";
      });
    }
  }
}

function buildToolbar(onChange, initial) {
  const bar = document.createElement("div");
  bar.className = "type-filter";
  bar.appendChild(
    Object.assign(document.createElement("span"), {
      className: "type-filter-label",
      textContent: "Show:",
    }),
  );
  const buttons = [];
  for (const f of FILTERS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "type-filter-btn";
    btn.textContent = f.label;
    btn.dataset.value = f.value;
    if (f.value === initial) btn.classList.add("active");
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.toggle("active", b === btn));
      onChange(f.value);
    });
    buttons.push(btn);
    bar.appendChild(btn);
  }
  return bar;
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector(".md-typeset");
  if (!root || !root.querySelector(".type-badge")) return; // not a digest page

  const sections = buildSections(root);
  if (!sections.some((s) => s.entries.length)) return;

  let current = sessionStorage.getItem(FILTER_KEY) || "all";
  if (!FILTERS.some((f) => f.value === current)) current = "all";

  const onChange = (value) => {
    current = value;
    sessionStorage.setItem(FILTER_KEY, value);
    applyFilter(sections, value);
  };

  const toolbar = buildToolbar(onChange, current);
  const h1 = root.querySelector("h1");
  if (h1 && h1.nextSibling) root.insertBefore(toolbar, h1.nextSibling);
  else root.insertBefore(toolbar, root.firstChild);

  applyFilter(sections, current);
});
