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

  // ── Footer "Subscribe" link scrolls to the nearest signup form
  document.querySelectorAll("[data-scroll-subscribe]").forEach((a) => {
    a.addEventListener("click", (e) => {
      const target = document.querySelector(".subscribe");
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth" });
      target.querySelector("input")?.focus({ preventScroll: true });
    });
  });

  // ── Newsletter
  document.querySelectorAll(".subscribe-form").forEach((form) => {
    const msg = form.querySelector(".subscribe-msg");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = form.email.value.trim();
      msg.className = "subscribe-msg";
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
        msg.textContent = "Please enter a valid email address.";
        msg.classList.add("err");
        return;
      }
      const btn = form.querySelector("button");
      btn.disabled = true;
      try {
        const res = await post("/api/subscribe", { email, source: form.dataset.source || null });
        if (!res.ok) throw new Error((await res.json()).detail || "Something went wrong.");
        msg.textContent = "You're in! Watch your inbox for the next cook.";
        msg.classList.add("ok");
        form.reset();
      } catch (err) {
        msg.textContent = err.message || "Something went wrong. Try again in a minute.";
        msg.classList.add("err");
      } finally {
        btn.disabled = false;
      }
    });
  });

  // ── Print just the recipe card
  document.querySelectorAll("[data-print]").forEach((b) => {
    b.addEventListener("click", () => {
      document.body.classList.add("printing");
      window.print();
    });
  });
  window.addEventListener("afterprint", () => document.body.classList.remove("printing"));
})();
