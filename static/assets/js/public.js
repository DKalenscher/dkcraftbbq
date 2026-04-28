(function () {
  "use strict";

  // ── Session ID (persists for the tab lifetime) ──────────────────────────────
  function makeUUID() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = Math.random() * 16 | 0;
      return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  const SESSION_ID = sessionStorage.getItem("bbq_sid") || (() => {
    const id = makeUUID();
    sessionStorage.setItem("bbq_sid", id);
    return id;
  })();

  // ── UTM params from current URL ─────────────────────────────────────────────
  const urlParams = new URLSearchParams(location.search);
  const UTM = {
    source: urlParams.get("utm_source"),
    medium: urlParams.get("utm_medium"),
    campaign: urlParams.get("utm_campaign"),
    content: urlParams.get("utm_content"),
    term: urlParams.get("utm_term"),
  };

  // ── Apply theme from settings object ────────────────────────────────────────
  function applyTheme(s) {
    const r = document.documentElement;
    if (s.primary_color)            r.style.setProperty("--primary", s.primary_color);
    if (s.secondary_color)          r.style.setProperty("--secondary", s.secondary_color);
    if (s.background_color)         r.style.setProperty("--bg", s.background_color);
    if (s.background_gradient_end)  r.style.setProperty("--bg-end", s.background_gradient_end);
    if (s.text_color)               r.style.setProperty("--text", s.text_color);
    if (s.card_background)          r.style.setProperty("--card-bg", s.card_background);
    if (s.accent_color)             r.style.setProperty("--accent", s.accent_color);

    const style = s.background_style || "gradient";
    document.body.classList.remove("bg-solid", "bg-pattern");
    if (style === "solid") document.body.classList.add("bg-solid");
    if (style === "pattern") document.body.classList.add("bg-pattern");

    if (s.custom_css) {
      document.getElementById("custom-css-injector").textContent = s.custom_css;
    }
  }

  // ── Render the sections and links ───────────────────────────────────────────
  function renderSections(sections) {
    const container = document.getElementById("sections-container");
    if (!sections.length) {
      container.innerHTML = '<p style="text-align:center;opacity:.5;padding:60px 0">No links yet.</p>';
      return;
    }

    container.innerHTML = sections.map(section => `
      <div class="section">
        <div class="section-header">
          <div class="section-title">${escHtml(section.title)}</div>
          ${section.description ? `<div class="section-desc">${escHtml(section.description)}</div>` : ""}
        </div>
        ${section.links.map(link => renderLinkCard(link)).join("")}
      </div>
    `).join("");

    container.querySelectorAll(".link-card").forEach(card => {
      card.addEventListener("click", (e) => {
        e.preventDefault();
        const linkId = parseInt(card.dataset.id, 10);
        const url = card.dataset.url;
        trackClick(linkId, url);
      });
    });
  }

  function renderLinkCard(link) {
    const thumb = link.image_url
      ? `<img class="link-thumb" src="${escAttr(link.image_url)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><div class="link-thumb-placeholder" style="display:none">🛒</div>`
      : `<div class="link-thumb-placeholder">🛒</div>`;

    return `
      <a class="link-card" href="${escAttr(link.url)}" data-id="${link.id}" data-url="${escAttr(link.url)}" target="_blank" rel="noopener noreferrer nofollow">
        ${thumb}
        <div class="link-info">
          <div class="link-title">${escHtml(link.title)}</div>
          ${link.description ? `<div class="link-desc">${escHtml(link.description)}</div>` : ""}
        </div>
        <span class="link-arrow">→</span>
      </a>
    `;
  }

  // ── Track page view ─────────────────────────────────────────────────────────
  function trackPageView() {
    fetch("/api/track/pageview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        referrer: document.referrer || null,
        utm_source: UTM.source,
        utm_medium: UTM.medium,
        utm_campaign: UTM.campaign,
        utm_content: UTM.content,
        utm_term: UTM.term,
        screen_width: screen.width,
        screen_height: screen.height,
        session_id: SESSION_ID,
        user_agent: navigator.userAgent,
      }),
    }).catch(() => {});
  }

  // ── Track link click ────────────────────────────────────────────────────────
  function trackClick(linkId, url) {
    fetch("/api/track/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        link_id: linkId,
        referrer: document.referrer || null,
        utm_source: UTM.source,
        utm_medium: UTM.medium,
        utm_campaign: UTM.campaign,
        utm_content: UTM.content,
        session_id: SESSION_ID,
        user_agent: navigator.userAgent,
      }),
    }).catch(() => {}).finally(() => {
      window.open(url, "_blank", "noopener,noreferrer");
    });
  }

  // ── HTML escaping ────────────────────────────────────────────────────────────
  function escHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function escAttr(str) {
    if (!str) return "";
    return String(str).replace(/"/g,"&quot;").replace(/'/g,"&#x27;");
  }

  // ── Main ─────────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const res = await fetch("/api/public/data");
      const { settings, sections } = await res.json();

      // Apply settings
      const titleEl = document.getElementById("site-title");
      const taglineEl = document.getElementById("site-tagline");
      const footerEl = document.getElementById("footer-title");
      if (settings.site_title) {
        document.title = settings.site_title;
        titleEl.textContent = settings.site_title;
        footerEl.textContent = settings.site_title;
      }
      if (settings.site_tagline) taglineEl.textContent = settings.site_tagline;

      if (settings.logo_path) {
        const logoImg = document.getElementById("site-logo");
        const placeholder = document.getElementById("logo-placeholder");
        logoImg.src = settings.logo_path;
        logoImg.style.display = "block";
        placeholder.style.display = "none";
      }

      applyTheme(settings);
      renderSections(sections);
      trackPageView();

    } catch (err) {
      document.getElementById("sections-container").innerHTML =
        '<p style="text-align:center;opacity:.4;padding:60px 0">Could not load links.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
