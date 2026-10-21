(() => {
  "use strict";

  // ── Session + UTM (same shape the v1 link page sends, so analytics stay continuous)
  let SESSION_ID = null;
  try {
    SESSION_ID = sessionStorage.getItem("bbq_sid");
    if (!SESSION_ID) {
      SESSION_ID = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
      sessionStorage.setItem("bbq_sid", SESSION_ID);
    }
  } catch (_) { /* storage blocked — track without a session id */ }

  const params = new URLSearchParams(location.search);
  const UTM = {
    utm_source: params.get("utm_source"),
    utm_medium: params.get("utm_medium"),
    utm_campaign: params.get("utm_campaign"),
    utm_content: params.get("utm_content"),
    utm_term: params.get("utm_term"),
  };

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    });
  }

  post("/api/track/pageview", {
    ...UTM,
    referrer: document.referrer || null,
    user_agent: navigator.userAgent,
    screen_width: screen.width,
    screen_height: screen.height,
    session_id: SESSION_ID,
  }).catch(() => {});

  document.querySelectorAll("[data-link-id]").forEach((a) => {
    a.addEventListener("click", () => {
      const { utm_term, ...utm } = UTM;
      post("/api/track/click", {
        ...utm,
        link_id: Number(a.dataset.linkId),
        referrer: location.href,
        user_agent: navigator.userAgent,
        session_id: SESSION_ID,
      }).catch(() => {});
    });
  });

  // ── Header toggles
  function toggle(btn, panel, focusSel) {
    if (!btn || !panel) return;
    btn.addEventListener("click", () => {
      const open = panel.hidden;
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      if (open && focusSel) panel.querySelector(focusSel)?.focus();
    });
  }
  toggle(document.querySelector(".menu-toggle"), document.getElementById("mobile-nav"));
  toggle(document.querySelector(".search-toggle"), document.getElementById("header-search"), "input");

})();
