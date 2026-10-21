// Recipes tab for the admin panel (v2)
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  let recipes = [];
  let categories = {};
  let editing = null;

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${localStorage.getItem("bbq_token")}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // ── Ingredient groups <-> textarea ("# Group" lines start a group)
  function ingredientsToText(groups) {
    return groups.map((g) => (g.group ? `# ${g.group}\n` : "") + g.items.join("\n")).join("\n");
  }
  function textToIngredients(text) {
    const groups = [];
    let cur = null;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      if (line.startsWith("#")) {
        cur = { group: line.replace(/^#+\s*/, ""), items: [] };
        groups.push(cur);
      } else {
        if (!cur) { cur = { group: "", items: [] }; groups.push(cur); }
        cur.items.push(line);
      }
    }
    return groups.filter((g) => g.items.length);
  }
  const lines = (t) => t.split("\n").map((l) => l.trim()).filter(Boolean);

  function render() {
    const reviewOnly = $("#recipes-review-only").checked;
    const rows = recipes.filter((r) => !reviewOnly || r.needs_review);
    const pending = recipes.filter((r) => r.needs_review).length;
    $("#recipes-count").textContent = `${recipes.length} recipes · ${pending} need review`;
    $("#recipes-list").innerHTML = rows.map((r) => `
      <div class="recipe-row" data-id="${r.id}">
        <img src="${esc(r.image)}" alt="" loading="lazy">
        <div class="recipe-row-main">
          <div class="recipe-row-title">${esc(r.title)}</div>
          <div class="recipe-row-meta">
            ${esc((categories[r.category] || [r.category])[0])} · ${Number(r.views).toLocaleString()} views
            ${r.needs_review ? '<span class="badge badge-primary">Needs review</span>' : ""}
            ${r.is_featured ? '<span class="badge badge-muted">Featured</span>' : ""}
            ${r.is_published ? "" : '<span class="badge badge-muted">Hidden</span>'}
          </div>
        </div>
        <a class="btn btn-ghost btn-sm" href="/recipe/${esc(r.slug)}" target="_blank" rel="noopener">View</a>
        <button class="btn btn-secondary btn-sm" data-edit="${r.id}">Edit</button>
      </div>`).join("") || '<p style="padding:22px">Nothing to review. Nice.</p>';
  }

  async function loadRecipes() {
    try {
      const data = await api("GET", "/api/admin/recipes");
      recipes = data.recipes;
      categories = data.categories;
      render();
    } catch (err) {
      $("#recipes-list").innerHTML = `<p style="padding:22px">Failed to load recipes: ${esc(err.message)}</p>`;
    }
  }

  function openEditor(id) {
    editing = recipes.find((r) => r.id === id);
    if (!editing) return;
    const f = $("#recipe-form");
    f.category.innerHTML = Object.entries(categories)
      .map(([slug, [name]]) => `<option value="${esc(slug)}">${esc(name)}</option>`).join("");
    for (const k of ["title", "category", "summary", "article", "prep_time", "cook_time",
      "total_time", "yield_text", "method", "wood", "review_notes"]) {
      f[k].value = editing[k] ?? "";
    }
    f.ingredients.value = ingredientsToText(editing.ingredients);
    f.steps.value = editing.steps.join("\n");
    f.notes.value = editing.notes.join("\n");
    f.needs_review.checked = editing.needs_review;
    f.is_featured.checked = editing.is_featured;
    f.is_published.checked = editing.is_published;
    $("#rf-review-box").innerHTML = editing.needs_review && editing.review_notes
      ? `<strong>What was filled in:</strong> ${esc(editing.review_notes)}` : "";
    $("#rf-review-box").hidden = !(editing.needs_review && editing.review_notes);
    $("#recipe-modal-title").textContent = editing.title;
    $("#recipe-modal").classList.add("open");
  }

  async function save() {
    const f = $("#recipe-form");
    const body = {
      title: f.title.value.trim(),
      category: f.category.value,
      summary: f.summary.value.trim(),
      article: f.article.value.trim(),
      ingredients: textToIngredients(f.ingredients.value),
      steps: lines(f.steps.value),
      notes: lines(f.notes.value),
      review_notes: f.review_notes.value.trim() || null,
      needs_review: f.needs_review.checked,
      is_featured: f.is_featured.checked,
      is_published: f.is_published.checked,
    };
    for (const k of ["prep_time", "cook_time", "total_time", "yield_text", "method", "wood"]) {
      body[k] = f[k].value.trim() || null;
    }
    const btn = $("#recipe-save");
    btn.disabled = true;
    try {
      const updated = await api("PUT", `/api/admin/recipes/${editing.id}`, body);
      recipes = recipes.map((r) => (r.id === updated.id ? updated : r));
      render();
      $("#recipe-modal").classList.remove("open");
    } catch (err) {
      alert("Save failed: " + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  document.querySelector('.nav-item[data-tab="recipes"]')?.addEventListener("click", loadRecipes);
  $("#recipes-review-only").addEventListener("change", render);
  $("#recipes-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-edit]");
    if (b) openEditor(Number(b.dataset.edit));
  });
  document.querySelectorAll("[data-close-recipe]").forEach((b) =>
    b.addEventListener("click", () => $("#recipe-modal").classList.remove("open")));
  $("#recipe-save").addEventListener("click", save);
})();
