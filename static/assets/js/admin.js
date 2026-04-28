(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────────
  let TOKEN = localStorage.getItem("bbq_token");
  let sections = [];
  let trendChart = null;
  let sourcesChart = null;
  let trendAnalyticsChart = null;
  let sourcesAnalyticsChart = null;
  let pendingDeleteFn = null;

  // ── API helpers ──────────────────────────────────────────────────────────────
  async function api(method, path, body, isFormData = false) {
    const opts = {
      method,
      headers: { Authorization: `Bearer ${TOKEN}` },
    };
    if (body && !isFormData) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    } else if (body && isFormData) {
      opts.body = body;
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      logout();
      return null;
    }
    if (res.status === 204) return null;
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Unknown error" }));
      throw new Error(err.detail || JSON.stringify(err));
    }
    return res.json();
  }

  // ── Toast ────────────────────────────────────────────────────────────────────
  function toast(msg, type = "info") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    document.getElementById("toast-container").appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  // ── Sidebar toggle ────────────────────────────────────────────────────────────
  const sidebar = document.getElementById("sidebar");
  const overlay = document.getElementById("sidebar-overlay");
  const isMobile = () => window.innerWidth <= 768;

  function initSidebar() {
    if (!isMobile() && localStorage.getItem("sidebar_collapsed") === "true") {
      sidebar.classList.add("collapsed");
    }
  }

  function closeMobileSidebar() {
    sidebar.classList.remove("mobile-open");
    overlay.classList.remove("visible");
  }

  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    if (isMobile()) {
      sidebar.classList.toggle("mobile-open");
      overlay.classList.toggle("visible");
    } else {
      sidebar.classList.toggle("collapsed");
      localStorage.setItem("sidebar_collapsed", sidebar.classList.contains("collapsed"));
    }
  });

  overlay.addEventListener("click", closeMobileSidebar);
  window.addEventListener("resize", () => {
    if (!isMobile()) closeMobileSidebar();
  });

  // ── Auth ─────────────────────────────────────────────────────────────────────
  function logout() {
    localStorage.removeItem("bbq_token");
    localStorage.removeItem("bbq_username");
    TOKEN = null;
    document.getElementById("app").style.display = "none";
    document.getElementById("auth-screen").style.display = "flex";
  }

  // Pre-fill saved username
  const savedUsername = localStorage.getItem("bbq_username");
  if (savedUsername) {
    document.getElementById("login-username").value = savedUsername;
    document.getElementById("remember-me").checked = true;
  }

  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("login-error");
    errEl.style.display = "none";
    const username = document.getElementById("login-username").value;
    const password = document.getElementById("login-password").value;
    const rememberMe = document.getElementById("remember-me").checked;
    try {
      const res = await fetch("/api/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, remember_me: rememberMe }),
      });
      if (!res.ok) {
        const err = await res.json();
        errEl.textContent = err.detail || "Login failed";
        errEl.style.display = "block";
        return;
      }
      const data = await res.json();
      TOKEN = data.access_token;
      localStorage.setItem("bbq_token", TOKEN);
      if (rememberMe) {
        localStorage.setItem("bbq_username", username);
      } else {
        localStorage.removeItem("bbq_username");
      }
      document.getElementById("auth-screen").style.display = "none";
      document.getElementById("app").style.display = "flex";
      initSidebar();
      navigateTo("dashboard");
      loadDashboard();
    } catch {
      errEl.textContent = "Could not connect to server";
      errEl.style.display = "block";
    }
  });

  document.getElementById("logout-btn").addEventListener("click", logout);
  document.getElementById("view-site-btn").addEventListener("click", () => window.open("/", "_blank"));

  // ── Navigation ────────────────────────────────────────────────────────────────
  function navigateTo(tab) {
    document.querySelectorAll(".nav-item[data-tab]").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    document.querySelectorAll(".page").forEach(p => {
      p.classList.toggle("active", p.id === `page-${tab}`);
    });
    if (tab === "dashboard") loadDashboard();
    if (tab === "content")   loadContent();
    if (tab === "analytics") loadAnalytics();
    if (tab === "customize") loadCustomize();
  }

  document.querySelectorAll(".nav-item[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => {
      navigateTo(btn.dataset.tab);
      if (isMobile()) closeMobileSidebar();
    });
  });

  document.getElementById("refresh-dashboard-btn").addEventListener("click", loadDashboard);

  // ── Modal helpers ─────────────────────────────────────────────────────────────
  function openModal(id) { document.getElementById(id).classList.add("open"); }
  function closeModal(id) { document.getElementById(id).classList.remove("open"); }

  document.querySelectorAll("[data-close-modal]").forEach(btn => {
    btn.addEventListener("click", () => closeModal(btn.dataset.closeModal));
  });

  // ── Confirm delete ────────────────────────────────────────────────────────────
  function confirmDelete(message, onConfirm) {
    document.getElementById("confirm-message").textContent = message;
    pendingDeleteFn = onConfirm;
    openModal("confirm-modal");
  }

  document.getElementById("confirm-delete-btn").addEventListener("click", async () => {
    if (pendingDeleteFn) {
      await pendingDeleteFn();
      pendingDeleteFn = null;
    }
    closeModal("confirm-modal");
  });

  // ── Dashboard ─────────────────────────────────────────────────────────────────
  async function loadDashboard() {
    try {
      const [overview, daily, topLinks, sources] = await Promise.all([
        api("GET", "/api/admin/analytics/overview"),
        api("GET", "/api/admin/analytics/daily?days=30"),
        api("GET", "/api/admin/analytics/top-links?limit=5"),
        api("GET", "/api/admin/analytics/traffic-sources?days=30"),
      ]);
      if (!overview) return;

      document.getElementById("stat-total-visits").textContent = fmt(overview.total_visits);
      document.getElementById("stat-unique").textContent = fmt(overview.unique_visitors);
      document.getElementById("stat-clicks").textContent = fmt(overview.total_clicks);
      document.getElementById("stat-today").textContent = fmt(overview.visits_today);
      document.getElementById("stat-week").textContent = fmt(overview.visits_this_week);
      document.getElementById("stat-month").textContent = fmt(overview.visits_this_month);

      renderTrendChart("chart-trend", daily, true);
      renderSourcesChart("chart-sources", sources, true);

      const tbody = document.getElementById("top-links-table");
      tbody.innerHTML = topLinks.length
        ? topLinks.map(l => `<tr><td>${escHtml(l.title)}</td><td style="text-align:right"><span class="badge badge-primary">${fmt(l.clicks)}</span></td></tr>`).join("")
        : '<tr><td colspan="2" style="text-align:center;opacity:.5">No click data yet</td></tr>';
    } catch (err) {
      toast("Failed to load dashboard: " + err.message, "error");
    }
  }

  // ── Analytics ─────────────────────────────────────────────────────────────────
  async function loadAnalytics() {
    const days = document.getElementById("analytics-range").value;
    try {
      const [daily, topLinks, sources, geo, utms] = await Promise.all([
        api("GET", `/api/admin/analytics/daily?days=${days}`),
        api("GET", `/api/admin/analytics/top-links?limit=10`),
        api("GET", `/api/admin/analytics/traffic-sources?days=${days}`),
        api("GET", `/api/admin/analytics/geo?days=${days}`),
        api("GET", `/api/admin/analytics/utm-campaigns?days=${days}`),
      ]);
      if (!daily) return;

      renderTrendChart("chart-trend-analytics", daily, false);
      renderSourcesChart("chart-sources-analytics", sources, false);

      const geoBody = document.getElementById("geo-table");
      geoBody.innerHTML = geo.length
        ? geo.map(r => `<tr><td>${escHtml(r.country)}</td><td style="text-align:right">${fmt(r.count)}</td></tr>`).join("")
        : '<tr><td colspan="2" style="text-align:center;opacity:.5">No data</td></tr>';

      const utmBody = document.getElementById("utm-table");
      utmBody.innerHTML = utms.length
        ? utms.map(r => `<tr><td>${escHtml(r.campaign)}</td><td>${escHtml(r.source)}</td><td style="text-align:right">${fmt(r.count)}</td></tr>`).join("")
        : '<tr><td colspan="3" style="text-align:center;opacity:.5">No UTM data</td></tr>';

      const topBody = document.getElementById("top-links-analytics-table");
      topBody.innerHTML = topLinks.length
        ? topLinks.map(l => `<tr><td>${escHtml(l.title)}</td><td style="text-align:right"><span class="badge badge-primary">${fmt(l.clicks)}</span></td></tr>`).join("")
        : '<tr><td colspan="2" style="text-align:center;opacity:.5">No data</td></tr>';

      // CSV export links
      document.getElementById("export-visits-btn").href = `/api/admin/analytics/export/visits?days=${days}`;
      document.getElementById("export-clicks-btn").href = `/api/admin/analytics/export/clicks?days=${days}`;
      document.getElementById("export-visits-btn").setAttribute("download", `visits_${days}d.csv`);
      document.getElementById("export-clicks-btn").setAttribute("download", `clicks_${days}d.csv`);

    } catch (err) {
      toast("Failed to load analytics: " + err.message, "error");
    }
  }

  document.getElementById("analytics-range").addEventListener("change", loadAnalytics);

  // ── Chart renderers ───────────────────────────────────────────────────────────
  function renderTrendChart(canvasId, data, isDashboard) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const chartRef = isDashboard ? "trendChart" : "trendAnalyticsChart";
    if (window[chartRef]) window[chartRef].destroy();

    window[chartRef] = new Chart(ctx, {
      type: "line",
      data: {
        labels: data.map(d => d.date.slice(5)),
        datasets: [
          {
            label: "Visits",
            data: data.map(d => d.visits),
            borderColor: "#E25822",
            backgroundColor: "rgba(226, 88, 34, 0.1)",
            tension: 0.3,
            fill: true,
            pointRadius: 2,
          },
          {
            label: "Clicks",
            data: data.map(d => d.clicks),
            borderColor: "#FF6B35",
            backgroundColor: "rgba(255, 107, 53, 0.08)",
            tension: 0.3,
            fill: true,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: "#f0ece8", font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: "#888", maxTicksLimit: 8 }, grid: { color: "rgba(255,255,255,0.05)" } },
          y: { ticks: { color: "#888" }, grid: { color: "rgba(255,255,255,0.05)" }, beginAtZero: true },
        },
      },
    });
  }

  function renderSourcesChart(canvasId, data, isDashboard) {
    const ctx = document.getElementById(canvasId).getContext("2d");
    const chartRef = isDashboard ? "sourcesChart" : "sourcesAnalyticsChart";
    if (window[chartRef]) window[chartRef].destroy();

    const colors = ["#E25822","#FF6B35","#8B4513","#c0392b","#e67e22","#f39c12","#d35400","#a04000"];
    window[chartRef] = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: data.map(d => d.source.slice(0, 25)),
        datasets: [{
          data: data.map(d => d.count),
          backgroundColor: colors,
          borderWidth: 2,
          borderColor: "#1a1a1a",
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "bottom", labels: { color: "#f0ece8", font: { size: 11 }, padding: 10 } },
        },
      },
    });
  }

  // ── Content: Sections & Links ─────────────────────────────────────────────────
  async function loadContent() {
    const container = document.getElementById("sections-editor");
    container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    try {
      sections = await api("GET", "/api/admin/sections");
      if (!sections) return;
      renderSectionsEditor();
    } catch (err) {
      container.innerHTML = `<p style="color:var(--danger);padding:20px">Error: ${escHtml(err.message)}</p>`;
    }
  }

  function renderSectionsEditor() {
    const container = document.getElementById("sections-editor");
    if (!sections.length) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">🔥</div>
          <h3>No sections yet</h3>
          <p>Click "Add Section" to get started</p>
        </div>`;
      return;
    }

    container.innerHTML = sections.map(section => buildSectionPanel(section)).join("");

    // Make sections sortable
    new Sortable(container, {
      animation: 150,
      handle: ".section-drag-handle",
      ghostClass: "sortable-ghost",
      onEnd: async () => {
        const ids = [...container.querySelectorAll(".section-panel")].map(el => parseInt(el.dataset.id));
        await api("POST", "/api/admin/sections/reorder", { ordered_ids: ids });
      },
    });

    // Make each link list sortable
    container.querySelectorAll(".links-list").forEach(list => {
      new Sortable(list, {
        animation: 150,
        handle: ".link-drag-handle",
        ghostClass: "sortable-ghost",
        onEnd: async () => {
          const ids = [...list.querySelectorAll(".link-row")].map(el => parseInt(el.dataset.id));
          await api("POST", "/api/admin/links/reorder", { ordered_ids: ids });
        },
      });
    });

    // Attach event listeners
    container.querySelectorAll("[data-edit-section]").forEach(btn => {
      btn.addEventListener("click", () => openSectionModal(parseInt(btn.dataset.editSection)));
    });
    container.querySelectorAll("[data-delete-section]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.deleteSection);
        const sec = sections.find(s => s.id === id);
        confirmDelete(`Delete section "${sec?.title}"? All links inside will also be deleted.`, async () => {
          await api("DELETE", `/api/admin/sections/${id}`);
          toast("Section deleted", "success");
          loadContent();
        });
      });
    });
    container.querySelectorAll("[data-add-link]").forEach(btn => {
      btn.addEventListener("click", () => openLinkModal(null, parseInt(btn.dataset.addLink)));
    });
    container.querySelectorAll("[data-edit-link]").forEach(btn => {
      btn.addEventListener("click", () => openLinkModal(parseInt(btn.dataset.editLink)));
    });
    container.querySelectorAll("[data-delete-link]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.deleteLink);
        const link = sections.flatMap(s => s.links).find(l => l.id === id);
        confirmDelete(`Delete link "${link?.title}"?`, async () => {
          await api("DELETE", `/api/admin/links/${id}`);
          toast("Link deleted", "success");
          loadContent();
        });
      });
    });
    container.querySelectorAll("[data-toggle-section]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = parseInt(btn.dataset.toggleSection);
        const sec = sections.find(s => s.id === id);
        await api("PUT", `/api/admin/sections/${id}`, { is_visible: !sec.is_visible });
        loadContent();
      });
    });
    container.querySelectorAll("[data-toggle-link]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const id = parseInt(btn.dataset.toggleLink);
        const link = sections.flatMap(s => s.links).find(l => l.id === id);
        await api("PUT", `/api/admin/links/${id}`, { is_visible: !link.is_visible });
        loadContent();
      });
    });
  }

  function buildSectionPanel(section) {
    const linksHtml = section.links.map(link => `
      <div class="link-row" data-id="${link.id}">
        <span class="drag-handle link-drag-handle">⠿</span>
        ${link.image_url
          ? `<img class="link-row-thumb" src="${escAttr(link.image_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
          : `<div class="link-row-thumb-placeholder">🛒</div>`}
        <div class="link-row-info">
          <div class="link-row-title">${escHtml(link.title)}</div>
          <div class="link-row-url">${escAttr(link.url)}</div>
        </div>
        <span class="badge ${link.is_visible ? "badge-success" : "badge-muted"}">${link.is_visible ? "Visible" : "Hidden"}</span>
        <span class="badge badge-primary">${fmt(link.click_count)} clicks</span>
        <div class="link-row-actions">
          <button class="btn btn-ghost btn-icon btn-sm" data-toggle-link="${link.id}" title="Toggle visibility">👁</button>
          <button class="btn btn-ghost btn-icon btn-sm" data-edit-link="${link.id}" title="Edit">✏️</button>
          <button class="btn btn-ghost btn-icon btn-sm" data-delete-link="${link.id}" title="Delete">🗑️</button>
        </div>
      </div>
    `).join("");

    return `
      <div class="section-panel" data-id="${section.id}">
        <div class="section-panel-header">
          <span class="drag-handle section-drag-handle">⠿</span>
          <div>
            <div class="section-panel-title">${escHtml(section.title)}</div>
            ${section.description ? `<div class="section-panel-desc">${escHtml(section.description)}</div>` : ""}
          </div>
          <span class="badge ${section.is_visible ? "badge-success" : "badge-muted"}" style="margin-left:8px">${section.is_visible ? "Visible" : "Hidden"}</span>
          <div class="section-panel-actions">
            <button class="btn btn-ghost btn-icon btn-sm" data-toggle-section="${section.id}" title="Toggle">👁</button>
            <button class="btn btn-ghost btn-icon btn-sm" data-edit-section="${section.id}" title="Edit">✏️</button>
            <button class="btn btn-ghost btn-icon btn-sm" data-delete-section="${section.id}" title="Delete">🗑️</button>
          </div>
        </div>
        <div class="links-list">
          ${linksHtml}
          <button class="add-link-btn" data-add-link="${section.id}">+ Add Link</button>
        </div>
      </div>
    `;
  }

  // ── Section modal ─────────────────────────────────────────────────────────────
  document.getElementById("add-section-btn").addEventListener("click", () => openSectionModal(null));

  function openSectionModal(id) {
    const section = id ? sections.find(s => s.id === id) : null;
    document.getElementById("section-modal-title").textContent = section ? "Edit Section" : "Add Section";
    document.getElementById("section-id").value = id || "";
    document.getElementById("section-title-input").value = section?.title || "";
    document.getElementById("section-desc-input").value = section?.description || "";
    document.getElementById("section-visible-input").checked = section ? section.is_visible : true;
    openModal("section-modal");
  }

  document.getElementById("save-section-btn").addEventListener("click", async () => {
    const id = document.getElementById("section-id").value;
    const title = document.getElementById("section-title-input").value.trim();
    if (!title) { toast("Section title is required", "error"); return; }
    const body = {
      title,
      description: document.getElementById("section-desc-input").value.trim() || null,
      is_visible: document.getElementById("section-visible-input").checked,
    };
    try {
      if (id) {
        await api("PUT", `/api/admin/sections/${id}`, body);
        toast("Section updated", "success");
      } else {
        await api("POST", "/api/admin/sections", body);
        toast("Section created", "success");
      }
      closeModal("section-modal");
      loadContent();
    } catch (err) {
      toast("Error: " + err.message, "error");
    }
  });

  // ── Link modal ────────────────────────────────────────────────────────────────
  function openLinkModal(linkId, sectionId) {
    const link = linkId ? sections.flatMap(s => s.links).find(l => l.id === linkId) : null;
    document.getElementById("link-modal-title").textContent = link ? "Edit Link" : "Add Link";
    document.getElementById("link-id").value = linkId || "";
    document.getElementById("link-section-id").value = sectionId || link?.section_id || "";
    document.getElementById("link-url-input").value = link?.url || "";
    document.getElementById("link-title-input").value = link?.title || "";
    document.getElementById("link-desc-input").value = link?.description || "";
    document.getElementById("link-image-input").value = link?.image_url || "";
    document.getElementById("link-visible-input").checked = link ? link.is_visible : true;
    document.getElementById("fetch-status").style.display = "none";

    const imgPreview = document.getElementById("link-image-preview");
    if (link?.image_url) {
      imgPreview.src = link.image_url;
      imgPreview.style.display = "block";
    } else {
      imgPreview.style.display = "none";
    }

    // Populate section dropdown
    const sel = document.getElementById("link-section-select");
    sel.innerHTML = sections.map(s =>
      `<option value="${s.id}" ${s.id === (sectionId || link?.section_id) ? "selected" : ""}>${escHtml(s.title)}</option>`
    ).join("");

    openModal("link-modal");
  }

  // Image preview on URL change
  document.getElementById("link-image-input").addEventListener("input", (e) => {
    const img = document.getElementById("link-image-preview");
    const url = e.target.value.trim();
    if (url.startsWith("http")) {
      img.src = url;
      img.style.display = "block";
      img.onerror = () => { img.style.display = "none"; };
    } else {
      img.style.display = "none";
    }
  });

  // Amazon fetch
  document.getElementById("fetch-amazon-btn").addEventListener("click", async () => {
    const url = document.getElementById("link-url-input").value.trim();
    if (!url) { toast("Enter an Amazon URL first", "error"); return; }
    const btn = document.getElementById("fetch-amazon-btn");
    const status = document.getElementById("fetch-status");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    status.style.display = "none";
    try {
      const data = await api("POST", "/api/amazon/fetch", { url });
      if (!data) return;
      if (data.success) {
        if (data.title) document.getElementById("link-title-input").value = data.title;
        if (data.description) document.getElementById("link-desc-input").value = data.description.slice(0, 500);
        if (data.image_url) {
          document.getElementById("link-image-input").value = data.image_url;
          const img = document.getElementById("link-image-preview");
          img.src = data.image_url;
          img.style.display = "block";
        }
        status.textContent = "✓ Product data fetched successfully";
        status.style.color = "var(--success)";
      } else {
        status.textContent = "⚠ " + data.message;
        status.style.color = "var(--warning)";
      }
      status.style.display = "block";
    } catch (err) {
      status.textContent = "Error: " + err.message;
      status.style.color = "var(--danger)";
      status.style.display = "block";
    } finally {
      btn.disabled = false;
      btn.innerHTML = "🔍 Fetch";
    }
  });

  document.getElementById("save-link-btn").addEventListener("click", async () => {
    const id = document.getElementById("link-id").value;
    const title = document.getElementById("link-title-input").value.trim();
    const url = document.getElementById("link-url-input").value.trim();
    if (!title) { toast("Link title is required", "error"); return; }
    if (!url) { toast("URL is required", "error"); return; }
    const sectionId = parseInt(document.getElementById("link-section-select").value);
    const body = {
      section_id: sectionId,
      title,
      description: document.getElementById("link-desc-input").value.trim() || null,
      url,
      image_url: document.getElementById("link-image-input").value.trim() || null,
      is_visible: document.getElementById("link-visible-input").checked,
    };
    try {
      if (id) {
        await api("PUT", `/api/admin/links/${id}`, body);
        toast("Link updated", "success");
      } else {
        await api("POST", "/api/admin/links", body);
        toast("Link added", "success");
      }
      closeModal("link-modal");
      loadContent();
    } catch (err) {
      toast("Error: " + err.message, "error");
    }
  });

  // ── Customize ─────────────────────────────────────────────────────────────────
  let currentSettings = {};

  async function loadCustomize() {
    try {
      currentSettings = await api("GET", "/api/admin/settings") || {};
      document.getElementById("c-site-title").value = currentSettings.site_title || "";
      document.getElementById("c-site-tagline").value = currentSettings.site_tagline || "";
      document.getElementById("c-primary").value = currentSettings.primary_color || "#E25822";
      document.getElementById("c-secondary").value = currentSettings.secondary_color || "#8B4513";
      document.getElementById("c-accent").value = currentSettings.accent_color || "#FF6B35";
      document.getElementById("c-bg").value = currentSettings.background_color || "#1a1a1a";
      document.getElementById("c-bg-end").value = currentSettings.background_gradient_end || "#2d1810";
      document.getElementById("c-text").value = currentSettings.text_color || "#f5f0eb";
      document.getElementById("c-card-bg").value = currentSettings.card_background || "#2a1f1a";
      document.getElementById("c-bg-style").value = currentSettings.background_style || "gradient";
      document.getElementById("c-custom-css").value = currentSettings.custom_css || "";

      if (currentSettings.logo_path) {
        document.getElementById("logo-preview-img").src = currentSettings.logo_path;
        document.getElementById("logo-preview-wrap").style.display = "block";
      }

      updatePreview();
    } catch (err) {
      toast("Failed to load settings: " + err.message, "error");
    }
  }

  function updatePreview() {
    const frame = document.getElementById("preview-frame");
    const title = document.getElementById("c-site-title").value || "DKCraftBBQ";
    const tagline = document.getElementById("c-site-tagline").value || "Gear I Actually Use";
    const bg = document.getElementById("c-bg").value;
    const bgEnd = document.getElementById("c-bg-end").value;
    const primary = document.getElementById("c-primary").value;
    const text = document.getElementById("c-text").value;
    const cardBg = document.getElementById("c-card-bg").value;
    const style = document.getElementById("c-bg-style").value;

    const bgStyle = style === "solid"
      ? `background:${bg}`
      : `background:linear-gradient(160deg,${bg},${bgEnd})`;

    frame.style.cssText = `padding:16px;min-height:320px;${bgStyle}`;
    document.getElementById("prev-title").style.color = text;
    document.getElementById("prev-title").textContent = title;
    document.getElementById("prev-tagline").style.color = text;
    document.getElementById("prev-tagline").textContent = tagline;
    document.getElementById("prev-card").style.background = cardBg;
    document.getElementById("prev-card").style.borderColor = `${primary}40`;
    frame.querySelector("[style*='margin-left:auto']").style.color = primary;
  }

  ["c-site-title","c-site-tagline","c-primary","c-secondary","c-accent","c-bg","c-bg-end","c-text","c-card-bg","c-bg-style"]
    .forEach(id => document.getElementById(id).addEventListener("input", updatePreview));

  document.getElementById("logo-upload-area").addEventListener("click", () => {
    document.getElementById("logo-file-input").click();
  });

  document.getElementById("logo-file-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await api("POST", "/api/admin/settings/logo", form, true);
      if (res?.logo_path) {
        document.getElementById("logo-preview-img").src = res.logo_path;
        document.getElementById("logo-preview-wrap").style.display = "block";
        toast("Logo uploaded", "success");
      }
    } catch (err) {
      toast("Upload failed: " + err.message, "error");
    }
  });

  document.getElementById("save-customize-btn").addEventListener("click", async () => {
    const body = {
      site_title: document.getElementById("c-site-title").value,
      site_tagline: document.getElementById("c-site-tagline").value,
      primary_color: document.getElementById("c-primary").value,
      secondary_color: document.getElementById("c-secondary").value,
      accent_color: document.getElementById("c-accent").value,
      background_color: document.getElementById("c-bg").value,
      background_gradient_end: document.getElementById("c-bg-end").value,
      text_color: document.getElementById("c-text").value,
      card_background: document.getElementById("c-card-bg").value,
      background_style: document.getElementById("c-bg-style").value,
      custom_css: document.getElementById("c-custom-css").value,
    };
    try {
      await api("PUT", "/api/admin/settings", body);
      toast("Settings saved", "success");
    } catch (err) {
      toast("Error: " + err.message, "error");
    }
  });

  // ── Settings: Change password ──────────────────────────────────────────────────
  document.getElementById("change-password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("pw-error");
    const sucEl = document.getElementById("pw-success");
    errEl.style.display = "none";
    sucEl.style.display = "none";

    const current = document.getElementById("pw-current").value;
    const newPw = document.getElementById("pw-new").value;
    const confirm = document.getElementById("pw-confirm").value;

    if (newPw !== confirm) {
      errEl.textContent = "New passwords do not match";
      errEl.style.display = "block";
      return;
    }

    try {
      await api("POST", "/api/auth/change-password", {
        current_password: current,
        new_password: newPw,
      });
      sucEl.textContent = "Password updated successfully";
      sucEl.style.display = "block";
      document.getElementById("change-password-form").reset();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.style.display = "block";
    }
  });

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function fmt(n) {
    if (n == null) return "0";
    return Number(n).toLocaleString();
  }

  function escHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  function escAttr(str) {
    if (!str) return "";
    return String(str).replace(/"/g,"&quot;");
  }

  // ── Init ─────────────────────────────────────────────────────────────────────
  if (TOKEN) {
    document.getElementById("auth-screen").style.display = "none";
    document.getElementById("app").style.display = "flex";
    initSidebar();
    loadDashboard();
  }

})();
