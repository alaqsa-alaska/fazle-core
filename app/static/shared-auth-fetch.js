/**
 * Fazle Core — shared X-Internal-Key fetch helpers.
 *
 * Extracted 2026-07-13: 6 dashboard pages (dashboard.html, payroll.html,
 * drafts.html, escort-roster.html, wa_chat.html, kb.html, open-chat.html)
 * each hand-rolled their own version of this exact boilerplate — building
 * the auth header, parsing FastAPI's {detail: "..."} / {detail: [...]}
 * error shape, throwing a readable Error on non-ok responses.
 *
 * Deliberately NOT a forced drop-in replacement for every page's existing
 * wrapper function: several pages have real behavioral differences on top
 * of this common core (dashboard.html's api() auto-JSON-serializes non-
 * string bodies and calls logout() on 403; wa_chat.html's apiFetch()
 * distinguishes 429/5xx and returns the raw Response instead of parsed
 * JSON). Those differences are legitimate, not just naming — this file
 * gives every page the same primitives to build on, not a mandate to
 * behave identically. Each page keeps its own API_KEY variable name,
 * localStorage key, and function names; only the actual duplicated
 * boilerplate moves here.
 *
 * Usage (see escort-roster.html for a full worked example):
 *   <script src="/static/shared-auth-fetch.js"></script>
 *   const r = await FazleAuthFetch.fetch('/admin/overview', API_KEY);
 *   if (!r.ok) throw new Error(await FazleAuthFetch.readError(r));
 *   const data = await r.json();
 *
 * or, for the common "GET/POST JSON, throw a readable error" shape:
 *   const data = await FazleAuthFetch.getJson('/admin/overview', API_KEY);
 *   const data = await FazleAuthFetch.postJson('/admin/bills/generate', API_KEY, body);
 */
(function (global) {
  'use strict';

  /**
   * Build the standard auth + content-type header set. Callers can spread
   * their own headers over the result to override/extend.
   */
  function buildHeaders(apiKey, extraHeaders) {
    return { 'X-Internal-Key': apiKey, 'Content-Type': 'application/json', ...(extraHeaders || {}) };
  }

  /**
   * fetch() with the auth header applied. Returns the raw Response —
   * callers decide how to handle non-ok status (some pages need to
   * special-case 403/429, which this intentionally does not do for them).
   */
  function fazleFetch(path, apiKey, opts) {
    opts = opts || {};
    const headers = buildHeaders(apiKey, opts.headers);
    return fetch(path, { ...opts, headers });
  }

  /**
   * Extract a human-readable error message from a non-ok Response, per
   * FastAPI's error shape: {detail: "string"} or {detail: [{msg: "..."}]}.
   * Falls back to raw response text, then to "<status> <statusText>".
   */
  async function readError(response) {
    const text = await response.text();
    if (!text) return `${response.status} ${response.statusText}`.trim();
    try {
      const data = JSON.parse(text);
      if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail.trim();
      if (Array.isArray(data?.detail) && data.detail.length) {
        return data.detail.map((item) => item?.msg || item?.message || JSON.stringify(item)).join('; ');
      }
    } catch (_) {
      /* not JSON — text fallback below */
    }
    return text;
  }

  /** GET path, parse JSON, throw a readable Error on non-ok. */
  async function getJson(path, apiKey, opts) {
    const r = await fazleFetch(path, apiKey, opts);
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
  }

  /** POST/PUT/PATCH/DELETE with a JSON body, parse JSON response, throw a readable Error on non-ok. */
  async function sendJson(method, path, apiKey, body, opts) {
    opts = opts || {};
    const r = await fazleFetch(path, apiKey, { ...opts, method, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
  }

  const postJson = (path, apiKey, body, opts) => sendJson('POST', path, apiKey, body, opts);
  const putJson = (path, apiKey, body, opts) => sendJson('PUT', path, apiKey, body, opts);
  const patchJson = (path, apiKey, body, opts) => sendJson('PATCH', path, apiKey, body, opts);

  global.FazleAuthFetch = {
    buildHeaders,
    fetch: fazleFetch,
    readError,
    getJson,
    postJson,
    putJson,
    patchJson,
  };
})(window);
