const SITE_URL = process.env.RAMBLEFIX_SITE_URL || "https://ramblefix.app/";
const WORKER_URL = process.env.RAMBLEFIX_DOWNLOAD_WORKER_URL || "https://ramblefix-dl.ramblefix.workers.dev";
const PROBE_HEADER = { "x-ramblefix-download-probe": "smoke" };

const ASSETS = [
  {
    key: "lite",
    label: "English",
    url: `${WORKER_URL}/dl/lite`,
    filename: "RambleFix-Lite-0.1.0.dmg",
    bytes: 2335416519,
    siteOccurrences: 2,
  },
  {
    key: "hi",
    label: "English + Hindi",
    url: `${WORKER_URL}/dl/hi`,
    filename: "RambleFix-HI-0.1.0.dmg",
    bytes: 4112192464,
    siteOccurrences: 1,
  },
];

function fail(message) {
  throw new Error(message);
}

function assertEqual(actual, expected, label) {
  if (actual !== expected) fail(`${label}: expected ${expected}, got ${actual}`);
}

function assertIncludes(value, expected, label) {
  if (!value.includes(expected)) fail(`${label}: missing ${expected}`);
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function getHeader(response, name) {
  return response.headers.get(name) || "";
}

async function checkSiteLinks() {
  const response = await fetchWithTimeout(SITE_URL, { cache: "no-store" });
  assertEqual(response.status, 200, "site status");
  const html = await response.text();

  if (html.includes(".r2.dev")) {
    fail("site must not link directly to public r2.dev download URLs");
  }

  for (const asset of ASSETS) {
    const count = html.split(asset.url).length - 1;
    assertEqual(count, asset.siteOccurrences, `site ${asset.label} worker link count`);
  }
}

async function checkHead(asset) {
  const response = await fetchWithTimeout(asset.url, {
    method: "HEAD",
    headers: PROBE_HEADER,
    redirect: "manual",
  });
  assertEqual(response.status, 200, `${asset.label} HEAD status`);
  assertEqual(getHeader(response, "content-type"), "application/x-apple-diskimage", `${asset.label} content-type`);
  assertEqual(Number(getHeader(response, "content-length")), asset.bytes, `${asset.label} content-length`);
  assertIncludes(getHeader(response, "content-disposition"), asset.filename, `${asset.label} filename`);
  assertIncludes(getHeader(response, "accept-ranges"), "bytes", `${asset.label} byte range support`);
}

async function checkRange(asset) {
  const response = await fetchWithTimeout(asset.url, {
    headers: { ...PROBE_HEADER, range: "bytes=0-1023" },
    redirect: "manual",
  });
  assertEqual(response.status, 206, `${asset.label} range status`);
  assertEqual(Number(getHeader(response, "content-length")), 1024, `${asset.label} range content-length`);
  assertEqual(getHeader(response, "content-range"), `bytes 0-1023/${asset.bytes}`, `${asset.label} content-range`);
  assertEqual(getHeader(response, "content-type"), "application/x-apple-diskimage", `${asset.label} range content-type`);

  const bytes = new Uint8Array(await response.arrayBuffer());
  assertEqual(bytes.length, 1024, `${asset.label} range body length`);
  if (bytes.every((byte) => byte === 0)) fail(`${asset.label} range body is all zeroes`);
}

async function checkCountEndpoint() {
  const response = await fetchWithTimeout(`${WORKER_URL}/count`, { headers: PROBE_HEADER, cache: "no-store" });
  assertEqual(response.status, 200, "count status");
  const data = await response.json();
  if (typeof data.visible !== "boolean") fail("count endpoint must return a boolean visible field");
  if (data.visible && typeof data.downloads !== "number") fail("count endpoint visible response must include numeric downloads");
}

async function main() {
  await checkSiteLinks();
  await checkCountEndpoint();
  for (const asset of ASSETS) {
    await checkHead(asset);
    await checkRange(asset);
  }
  console.log("download smoke passed");
}

main().catch((error) => {
  console.error(`download smoke failed: ${error.message}`);
  process.exit(1);
});
