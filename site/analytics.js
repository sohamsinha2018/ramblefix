(function () {
  "use strict";

  if (navigator.doNotTrack === "1") return;

  var endpoint = "/api/track";
  var sessionKey = "ramblefix_site_session";

  function sessionId() {
    try {
      var existing = window.sessionStorage.getItem(sessionKey);
      if (existing) return existing;
      var created = window.crypto && window.crypto.randomUUID
        ? window.crypto.randomUUID()
        : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
      window.sessionStorage.setItem(sessionKey, created);
      return created;
    } catch (_) {
      return "ephemeral-" + Math.random().toString(16).slice(2);
    }
  }

  function capture(eventName, target) {
    var payload = JSON.stringify({
      event: eventName,
      target: target || "unknown",
      location: window.location.pathname,
      sessionId: sessionId()
    });

    try {
      if (navigator.sendBeacon) {
        var sent = navigator.sendBeacon(endpoint, new Blob([payload], { type: "application/json" }));
        if (sent) return;
      }
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
        credentials: "same-origin"
      }).catch(function () {});
    } catch (_) {}
  }

  document.querySelectorAll("[data-analytics-event]").forEach(function (element) {
    element.addEventListener("click", function () {
      capture(element.dataset.analyticsEvent, element.dataset.analyticsTarget);
    });
  });

  capture("site viewed", "home");
})();
