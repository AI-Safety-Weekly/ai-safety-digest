/* Password-gated feedback for AI Safety Digest.
 *
 * Click → modal opens → user enters password + types feedback → submit
 * POSTs JSON to a Cloudflare Worker, which validates the password and
 * appends the entry to a markdown file in the repo. Monday's cron reads
 * those entries and feeds them into the classifier prompt.
 *
 * The destination Worker URL is filled in at deploy time. Until then it
 * points at a placeholder and submissions will fail gracefully.
 */

// {{ Replace with the Worker URL after Cloudflare deploy. }}
const FEEDBACK_WORKER_URL = "https://ai-safety-digest-feedback.oodles-of-noodles.workers.dev/";

const PW_KEY = "feedback-pw";

function rememberedPassword() {
  return sessionStorage.getItem(PW_KEY) || "";
}

function setRememberedPassword(pw) {
  sessionStorage.setItem(PW_KEY, pw);
}

function clearRememberedPassword() {
  sessionStorage.removeItem(PW_KEY);
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

function openModal({ title, fields, onSubmit }) {
  // Cleanup any previous modal
  document.querySelectorAll(".feedback-modal-overlay").forEach((n) => n.remove());

  const overlay = el("div", { class: "feedback-modal-overlay" });
  const card = el("div", { class: "feedback-modal" });

  card.appendChild(el("h2", {}, title));

  const form = el("form", { class: "feedback-form" });
  const status = el("div", { class: "feedback-status" });

  const inputs = {};

  // Password input (always first, prefilled from session)
  const pwLabel = el("label", {}, "Password");
  const pwInput = el("input", {
    type: "password",
    name: "password",
    required: "true",
    autocomplete: "current-password",
    value: rememberedPassword(),
  });
  pwLabel.appendChild(pwInput);
  form.appendChild(pwLabel);
  inputs.password = pwInput;

  // Caller-specified fields
  for (const f of fields) {
    const label = el("label", {}, f.label);
    let input;
    if (f.kind === "textarea") {
      input = el("textarea", {
        name: f.name,
        rows: f.rows || 5,
        required: f.required ? "true" : "false",
        placeholder: f.placeholder || "",
      });
    } else if (f.kind === "select") {
      input = el("select", { name: f.name });
      for (const opt of f.options) {
        input.appendChild(el("option", { value: opt.value }, opt.label));
      }
      if (f.default) input.value = f.default;
    } else if (f.kind === "checkbox") {
      input = el("input", { type: "checkbox", name: f.name });
      label.classList.add("checkbox");
    } else {
      input = el("input", {
        type: f.kind || "text",
        name: f.name,
        required: f.required ? "true" : "false",
        placeholder: f.placeholder || "",
        value: f.value || "",
      });
    }
    label.appendChild(input);
    if (f.help) label.appendChild(el("small", {}, f.help));
    form.appendChild(label);
    inputs[f.name] = input;
  }

  const actions = el("div", { class: "feedback-actions" });
  const cancelBtn = el(
    "button",
    { type: "button", class: "feedback-btn feedback-btn-cancel" },
    "Cancel",
  );
  cancelBtn.addEventListener("click", () => overlay.remove());
  const submitBtn = el(
    "button",
    { type: "submit", class: "feedback-btn feedback-btn-submit" },
    "Submit",
  );
  actions.appendChild(cancelBtn);
  actions.appendChild(submitBtn);
  form.appendChild(actions);
  form.appendChild(status);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    submitBtn.disabled = true;
    submitBtn.textContent = "Sending…";
    status.textContent = "";
    status.className = "feedback-status";

    const values = { password: inputs.password.value };
    for (const f of fields) {
      const i = inputs[f.name];
      values[f.name] = f.kind === "checkbox" ? i.checked : i.value;
    }

    try {
      const result = await onSubmit(values);
      if (result.ok) {
        setRememberedPassword(values.password);
        status.textContent =
          "✓ Logged. Will be reviewed in Monday's run.";
        status.classList.add("ok");
        submitBtn.textContent = "Done";
        setTimeout(() => overlay.remove(), 1800);
        return;
      }
      status.textContent = "✗ " + (result.error || "Submission failed.");
      status.classList.add("err");
      if (/password/i.test(result.error || "")) clearRememberedPassword();
    } catch (err) {
      status.textContent = "✗ Network error — try again.";
      status.classList.add("err");
    }
    submitBtn.disabled = false;
    submitBtn.textContent = "Submit";
  });

  card.appendChild(form);
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  // Focus the first empty field
  const firstEmpty = Array.from(card.querySelectorAll("input, textarea")).find(
    (i) => !i.value && i.type !== "checkbox",
  );
  (firstEmpty || pwInput).focus();

  // Click outside to close
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
}

async function postFeedback(payload) {
  const resp = await fetch(FEEDBACK_WORKER_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try {
    data = await resp.json();
  } catch {
    /* ignore */
  }
  return { ok: resp.ok, error: data.error, status: resp.status };
}

function tierFeedback(paperUrl, paperTitle, claudeTier) {
  openModal({
    title: "Disagree with this tier?",
    fields: [
      {
        name: "suggested_tier",
        label: "Suggested tier",
        kind: "select",
        options: [
          { value: "", label: "— pick one —" },
          { value: "high", label: "High" },
          { value: "medium", label: "Medium" },
          { value: "low", label: "Low" },
        ],
      },
      {
        name: "body",
        label: "What did the bot get wrong?",
        kind: "textarea",
        required: true,
        placeholder: "e.g. This is a well-known capability paper, not safety.",
      },
      {
        name: "make_permanent",
        label: "Make this a permanent rule",
        kind: "checkbox",
        help: "If checked, the bot will promote this correction to its permanent rulebook on Monday.",
      },
    ],
    onSubmit: (values) =>
      postFeedback({
        password: values.password,
        type: "tier-disagreement",
        paper_url: paperUrl,
        paper_title: paperTitle,
        claude_tier: claudeTier,
        suggested_tier: values.suggested_tier,
        make_permanent: values.make_permanent,
        body: values.body,
      }),
  });
}

function missedPaper() {
  openModal({
    title: "Suggest a missed paper",
    fields: [
      {
        name: "paper_url",
        label: "arXiv URL or ID",
        kind: "text",
        required: true,
        placeholder: "e.g. https://arxiv.org/abs/2605.12345  or  2605.12345",
      },
      {
        name: "suggested_tier",
        label: "Suggested tier",
        kind: "select",
        options: [
          { value: "", label: "— pick one —" },
          { value: "high", label: "High" },
          { value: "medium", label: "Medium" },
          { value: "low", label: "Low" },
        ],
      },
      {
        name: "body",
        label: "Why it matters",
        kind: "textarea",
        required: true,
        placeholder: "Short note on why this paper should be in next week's digest.",
      },
      {
        name: "make_permanent",
        label: "Make this a permanent rule",
        kind: "checkbox",
        help: "Optional: if there's a pattern here (e.g. always include papers from <X>), check this so the bot adopts it for good.",
      },
    ],
    onSubmit: (values) =>
      postFeedback({
        password: values.password,
        type: "missed-paper",
        paper_url: values.paper_url,
        suggested_tier: values.suggested_tier,
        make_permanent: values.make_permanent,
        body: values.body,
      }),
  });
}

// Wire up links
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("a.tier-feedback").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      tierFeedback(el.dataset.url, el.dataset.title, el.dataset.tier);
    });
  });
  document.querySelectorAll("a.missed-paper").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      missedPaper();
    });
  });
});
