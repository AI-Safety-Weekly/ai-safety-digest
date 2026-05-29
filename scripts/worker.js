/*
 * Cloudflare Worker — feedback receiver for AI Safety Digest.
 *
 * Accepts JSON POST from the static site, validates the shared password,
 * and appends the feedback entry to a weekly markdown file in the GitHub
 * repo via the Contents API. Monday's cron reads those files and feeds
 * them to the classifier.
 *
 * Required Worker environment variables (set as Secrets in the dashboard):
 *   PW_HASH    — SHA-256 hex of the shared feedback password
 *   GH_TOKEN   — fine-grained GitHub PAT with Contents read+write on this repo only
 *
 * Optional:
 *   REPO       — defaults to "ai-safety-weekly/ai-safety-digest"
 *   BRANCH     — defaults to "main"
 *   ALLOWED_ORIGIN — defaults to "https://ai-safety-weekly.github.io"
 */

const DEFAULTS = {
  REPO: "ai-safety-weekly/ai-safety-digest",
  BRANCH: "main",
  ALLOWED_ORIGIN: "https://ai-safety-weekly.github.io",
};

function cfg(env, key) {
  return env[key] || DEFAULTS[key];
}

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": cfg(env, "ALLOWED_ORIGIN"),
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function jsonResponse(env, status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...corsHeaders(env), "Content-Type": "application/json" },
  });
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function isoWeekTag(d) {
  // Returns e.g. "2026-W22"
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dayNum = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - dayNum + 3);
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4));
  const weekNum = 1 + Math.round(
    ((date.getTime() - firstThursday.getTime()) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7,
  );
  return `${date.getUTCFullYear()}-W${String(weekNum).padStart(2, "0")}`;
}

function utf8ToBase64(str) {
  // Modern Workers expose btoa, but it doesn't handle multibyte chars correctly.
  return btoa(String.fromCharCode(...new TextEncoder().encode(str)));
}

function base64ToUtf8(b64) {
  const bin = atob(b64.replace(/\s/g, ""));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

async function appendToRepo(env, path, newEntry, commitMessage) {
  const repo = cfg(env, "REPO");
  const branch = cfg(env, "BRANCH");
  const apiBase = `https://api.github.com/repos/${repo}/contents/${path}`;
  const ghHeaders = {
    Authorization: `Bearer ${env.GH_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "ai-safety-digest-feedback-worker",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  // 1) Get current file (if any) to read sha + existing content
  const getResp = await fetch(`${apiBase}?ref=${branch}`, { headers: ghHeaders });
  let existing = "";
  let sha = null;
  if (getResp.status === 200) {
    const data = await getResp.json();
    sha = data.sha;
    existing = base64ToUtf8(data.content);
  } else if (getResp.status !== 404) {
    throw new Error(`GitHub GET ${path} → ${getResp.status}: ${await getResp.text()}`);
  }

  const updated = (existing.endsWith("\n") || existing === "" ? existing : existing + "\n") + newEntry;
  const putBody = {
    message: commitMessage,
    content: utf8ToBase64(updated),
    branch,
  };
  if (sha) putBody.sha = sha;

  const putResp = await fetch(apiBase, {
    method: "PUT",
    headers: { ...ghHeaders, "Content-Type": "application/json" },
    body: JSON.stringify(putBody),
  });
  if (!putResp.ok) {
    throw new Error(`GitHub PUT ${path} → ${putResp.status}: ${await putResp.text()}`);
  }
}

function buildEntry(payload, nowIso) {
  const lines = [`### ${nowIso}`, ""];
  const t = (payload.type || "general").toString();
  lines.push(`**Type:** ${t}`);
  if (payload.paper_url) lines.push(`**Paper:** ${payload.paper_url}`);
  if (payload.paper_title) lines.push(`**Title:** ${payload.paper_title}`);
  if (payload.claude_tier) lines.push(`**Claude's tier:** ${payload.claude_tier}`);
  if (payload.suggested_tier) lines.push(`**Suggested tier:** ${payload.suggested_tier}`);
  if (payload.make_permanent) lines.push(`**Make permanent rule:** yes`);
  lines.push("");
  lines.push((payload.body || "").toString().trim());
  lines.push("");
  lines.push("---");
  lines.push("");
  return lines.join("\n");
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }
    if (request.method !== "POST") {
      return jsonResponse(env, 405, { error: "Method not allowed" });
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return jsonResponse(env, 400, { error: "Invalid JSON" });
    }

    if (!env.PW_HASH || !env.GH_TOKEN) {
      return jsonResponse(env, 500, { error: "Worker not configured (missing PW_HASH or GH_TOKEN)" });
    }

    const pw = (payload.password || "").toString();
    if (!pw) return jsonResponse(env, 401, { error: "Missing password" });
    const hash = await sha256Hex(pw);
    if (hash !== env.PW_HASH.toLowerCase()) {
      return jsonResponse(env, 401, { error: "Wrong password" });
    }

    const text = (payload.body || "").toString().trim();
    if (text.length === 0) return jsonResponse(env, 400, { error: "Empty feedback body" });
    if (text.length > 10000) return jsonResponse(env, 400, { error: "Feedback too long (>10k chars)" });

    const now = new Date();
    const nowIso = now.toISOString();
    const week = isoWeekTag(now);
    const path = `feedback/${week}.md`;
    const entry = buildEntry(payload, nowIso);
    const commitMessage = `Feedback submitted ${nowIso}`;

    try {
      await appendToRepo(env, path, entry, commitMessage);
    } catch (e) {
      return jsonResponse(env, 502, { error: `Repo write failed: ${e.message}` });
    }

    return jsonResponse(env, 200, { ok: true });
  },
};
