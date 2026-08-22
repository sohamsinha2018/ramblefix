var allowedEvents = new Set([
  "site viewed",
  "site cta clicked",
  "download requested",
  "language vote clicked",
  "demo switched",
  "builderr clicked",
  "builderr challenge clicked",
  "builder profile clicked"
]);

function clean(value, fallback, maxLength) {
  if (typeof value !== "string") return fallback;
  var normalized = value.trim().slice(0, maxLength);
  return /^[a-zA-Z0-9_+./ -]+$/.test(normalized) ? normalized : fallback;
}

module.exports = async function handler(request, response) {
  response.setHeader("Cache-Control", "no-store");

  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    return response.status(405).json({ error: "method_not_allowed" });
  }

  var token = process.env.POSTHOG_PROJECT_TOKEN;
  if (!token) return response.status(503).json({ error: "analytics_not_configured" });

  var body = request.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch (_) { body = {}; }
  }
  body = body || {};

  var eventName = clean(body.event, "", 48);
  if (!allowedEvents.has(eventName)) return response.status(400).json({ error: "invalid_event" });

  var session = clean(body.sessionId, "ephemeral", 96);
  var target = clean(body.target, "unknown", 64);
  var location = clean(body.location, "/", 96);
  var host = (process.env.POSTHOG_HOST || "https://us.i.posthog.com").replace(/\/$/, "");

  try {
    var posthogResponse = await fetch(host + "/i/v0/e/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: token,
        event: eventName,
        distinct_id: "site-session-" + session,
        timestamp: new Date().toISOString(),
        properties: {
          $process_person_profile: false,
          target: target,
          location: location,
          surface: "ramblefix.app",
          analytics_mode: "explicit_anonymous_v1"
        }
      })
    });

    if (!posthogResponse.ok) return response.status(502).json({ error: "analytics_upstream_failed" });
    return response.status(202).json({ accepted: true });
  } catch (_) {
    return response.status(502).json({ error: "analytics_upstream_failed" });
  }
};
