// RambleFix download counter. Counts every download, streams the file, and only
// exposes the public number once it crosses THRESHOLD (so a small number never shows).
const THRESHOLD = 100;
const FILES = {
  lite: "RambleFix-Lite-0.1.0.dmg",
  hi: "RambleFix-HI-0.1.0.dmg",
};
const CORS = { "access-control-allow-origin": "*", "cache-control": "no-store" };

async function readCount(env, key) {
  const raw = await env.COUNTS.get(key);
  const n = parseInt(raw || "0", 10);
  return Number.isFinite(n) ? n : 0;
}

async function bump(env, key) {
  const per = await readCount(env, key) + 1;
  await env.COUNTS.put(key, String(per));
}

function responseHeaders(object, filename, rangeRequested) {
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("accept-ranges", "bytes");
  headers.set("cache-control", "public, max-age=3600");
  headers.set("content-disposition", `attachment; filename="${filename}"`);
  headers.set("content-type", "application/x-apple-diskimage");
  headers.set("etag", object.httpEtag);
  if (rangeRequested && object.range) {
    const start = object.range.offset;
    const end = start + object.range.length - 1;
    headers.set("content-length", String(object.range.length));
    headers.set("content-range", `bytes ${start}-${end}/${object.size}`);
  } else {
    headers.set("content-length", String(object.size));
  }
  return headers;
}

function objectOptions(req) {
  const options = { onlyIf: req.headers };
  if (req.headers.get("range")) options.range = req.headers;
  return options;
}

function isProbe(req) {
  return req.headers.get("x-ramblefix-download-probe") === "smoke";
}

export default {
  async fetch(req, env, ctx) {
    const p = new URL(req.url).pathname.replace(/\/+$/, "");
    if (p === "/count") {
      const n = await readCount(env, "lite") + await readCount(env, "hi");
      const body = n >= THRESHOLD ? { downloads: n, visible: true } : { visible: false };
      return new Response(JSON.stringify(body), { headers: { "content-type": "application/json", ...CORS } });
    }
    const key = p === "/dl/lite" ? "lite" : p === "/dl/hi" ? "hi" : null;
    if (key) {
      if (req.method !== "GET" && req.method !== "HEAD") {
        return new Response("method not allowed", { status: 405 });
      }

      const object = await env.DOWNLOADS.get(FILES[key], objectOptions(req));
      if (!object) return new Response("not found", { status: 404 });

      const rangeRequested = Boolean(req.headers.get("range"));
      if (req.method === "GET" && !isProbe(req)) ctx.waitUntil(bump(env, key));
      return new Response(req.method === "HEAD" ? null : object.body, {
        status: rangeRequested ? 206 : 200,
        headers: responseHeaders(object, FILES[key], rangeRequested),
      });
    }
    return new Response("not found", { status: 404 });
  }
};
