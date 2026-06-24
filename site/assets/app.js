const state = {
  summary: null,
  people: [],
  publications: [],
  peopleById: new Map(),
  selectedPublicationIds: new Set(),
  exportObjectUrls: [],
};

const numberFormatter = new Intl.NumberFormat("en-US");

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch (_error) {
    return "";
  }
  return "";
}

function formatNumber(value) {
  return numberFormatter.format(value || 0);
}

function formatOptionalNumber(value) {
  return Number.isInteger(value) && value >= 0 ? formatNumber(value) : "--";
}

function setSinceMetric(elementId, value, summary) {
  const element = document.getElementById(elementId);
  if (Number.isInteger(value) && value >= 0) {
    element.textContent = formatNumber(value);
    element.removeAttribute("title");
    element.classList.remove("is-pending");
    return;
  }

  const sinceYear = summary.since_year || 2021;
  const missing = summary.since_metrics_missing_publications || 0;
  element.textContent = "pending";
  element.title = missing
    ? `Needs ${formatNumber(missing)} more per-publication citation counts since ${sinceYear}.`
    : `Needs per-publication citation counts since ${sinceYear}.`;
  element.classList.add("is-pending");
}

function formatDate(value) {
  if (!value) return "Data has not been generated yet.";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `Last updated ${value}`;
  return `Updated ${date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
  })}`;
}

function initials(name) {
  return String(name || "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function textForSearch(pub) {
  return [
    pub.title,
    pub.venue,
    pub.doi,
    ...(pub.author_names || []),
    ...(pub.group_author_ids || []).map((id) => state.peopleById.get(id)?.name || id),
  ]
    .join(" ")
    .toLowerCase();
}

function publicationById(id) {
  return state.publications.find((pub) => pub.id === id);
}

function renderSummary() {
  const summary = state.summary || {};
  document.getElementById("publication-count").textContent = formatNumber(summary.publication_count);
  document.getElementById("member-count").textContent = formatNumber(summary.member_count);
  document.getElementById("total-citations").textContent = formatNumber(summary.total_citations);
  document.getElementById("h-index").textContent = formatNumber(summary.h_index);
  document.getElementById("i10-index").textContent = formatNumber(summary.i10_index);
  const sinceYear = summary.since_year || 2021;
  document.getElementById("since-heading").textContent = `Since ${sinceYear}`;
  document.getElementById("since-citations").textContent = formatOptionalNumber(summary.since_citations);
  setSinceMetric("since-h-index", summary.since_h_index, summary);
  setSinceMetric("since-i10-index", summary.since_i10_index, summary);
  document.getElementById("last-updated").textContent = formatDate(summary.generated_at);
  renderCitationChart(summary.profile_annual_citations || []);
}

function profileLink(label, url) {
  const href = safeUrl(url);
  if (!href) return "";
  return `<a href="${href}" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
}

function contiguousAnnualRows(annualCitations) {
  const byYear = new Map(
    (annualCitations || [])
      .filter((row) => Number.isInteger(row.year))
      .map((row) => [row.year, row]),
  );
  const years = [...byYear.keys()].sort((a, b) => a - b);
  if (!years.length) return [];

  const firstYear = years[0];
  const lastYear = years[years.length - 1];
  const rows = [];
  for (let year = firstYear; year <= lastYear; year += 1) {
    const row = byYear.get(year);
    rows.push({
      year,
      citations: row?.citations || 0,
      total: row?.total || 0,
    });
  }
  return rows;
}

function citationChartHtml(rows, options = {}) {
  if (!rows.length) {
    return "";
  }
  const plotHeight = options.plotHeight || 100;
  const totalHeight = plotHeight + 22;
  const columnWidth = options.columnWidth || "minmax(0, 1fr)";
  const minPlotWidth = options.minPlotWidth || 0;
  const maxCitations = Math.max(...rows.map((row) => row.citations || 0), 1);
  const tickStep = Math.max(1, Math.ceil(maxCitations / 4 / 5) * 5);
  const chartMax = tickStep * 4;
  const ticks = [4, 3, 2, 1, 0].map((multiple) => multiple * tickStep);

  return `
    <div class="chart-scroll">
      <div
        class="chart-plot"
        style="--chart-plot-height: ${plotHeight}px; --chart-total-height: ${totalHeight}px; ${minPlotWidth ? `min-width: ${minPlotWidth}px;` : ""}"
      >
        <div class="chart-grid" aria-hidden="true">
          ${ticks
            .map((tick) => `<span style="bottom: ${(tick / chartMax) * 100}%"></span>`)
            .join("")}
        </div>
        <div class="chart-bars" style="grid-template-columns: repeat(${rows.length}, ${columnWidth});">
          ${rows
            .map((row) => {
              const height = Math.max(8, Math.round(((row.citations || 0) / chartMax) * plotHeight));
              return `
                <div class="chart-bar" title="${escapeHtml(row.year)}: ${formatNumber(row.citations)} citations">
                  <span style="height: ${height}px"></span>
                  <em>${escapeHtml(row.year)}</em>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    </div>
    <div class="chart-axis" aria-hidden="true" style="height: ${plotHeight}px">
      ${ticks
        .map(
          (tick) => `
          <span style="bottom: ${(tick / chartMax) * 100}%">${formatNumber(tick)}</span>
        `,
        )
        .join("")}
    </div>
  `;
}

function renderCitationChart(annualCitations) {
  const chart = document.getElementById("citation-chart");
  const rows = contiguousAnnualRows(annualCitations).slice(-8);
  chart.innerHTML = citationChartHtml(rows);
}

function renderFullCitationChart() {
  const chart = document.getElementById("citation-modal-chart");
  const rows = contiguousAnnualRows(state.summary?.profile_annual_citations || []);
  chart.innerHTML = citationChartHtml(rows, {
    plotHeight: 145,
    columnWidth: "28px",
    minPlotWidth: Math.max(460, rows.length * 34),
  });
}

function openCitationModal() {
  const modal = document.getElementById("citation-modal");
  renderFullCitationChart();
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  document.getElementById("citation-modal-close").focus();
}

function closeCitationModal() {
  const modal = document.getElementById("citation-modal");
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  document.getElementById("citation-modal-open").focus();
}

function renderPeople() {
  const list = document.getElementById("people-list");
  list.innerHTML = state.people
    .map((person) => {
      const orcidUrl = person.orcid ? `https://orcid.org/${person.orcid}` : "";
      const scholarUrl = safeUrl(person.google_scholar_url);
      const memberName = scholarUrl
        ? `<a class="member-name" href="${scholarUrl}" rel="noopener noreferrer">${escapeHtml(person.name)}</a>`
        : `<span class="member-name">${escapeHtml(person.name)}</span>`;
      return `
        <article class="member-row">
          <div class="member-avatar" aria-hidden="true">${escapeHtml(initials(person.name))}</div>
          <div class="member-main">
            ${memberName}
            <div class="member-meta">
              ${formatNumber(person.works_count)} papers, ${formatNumber(person.citation_count)} citations, h-index ${formatNumber(person.h_index)}
            </div>
            <div class="member-links">
              ${profileLink("Scholar", person.google_scholar_url)}
              ${profileLink("ORCID", orcidUrl)}
            </div>
          </div>
          <span class="member-chevron" aria-hidden="true">›</span>
        </article>
      `;
    })
    .join("");

  const personFilter = document.getElementById("person-filter");
  personFilter.innerHTML = '<option value="">All people</option>';
  for (const person of state.people) {
    const option = document.createElement("option");
    option.value = person.id;
    option.textContent = person.name;
    personFilter.appendChild(option);
  }
}

function groupAuthorTags(pub) {
  const names = (pub.group_author_ids || [])
    .map((id) => state.peopleById.get(id)?.name || id)
    .sort((a, b) => a.localeCompare(b));
  if (!names.length) return "";
  return `<div class="group-authors">${names.map(escapeHtml).join(", ")}</div>`;
}

function csvCell(value) {
  const text = String(value ?? "").replace(/\r?\n/g, " ");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function selectedPublications() {
  return [...state.selectedPublicationIds]
    .map(publicationById)
    .filter(Boolean);
}

function selectedCsvContent(publications) {
  const header = [
    "title",
    "year",
    "citation_count",
    "citation_count_since_year",
    "venue",
    "authors",
    "group_authors",
    "doi",
    "url",
    "google_scholar_cites_url",
  ];
  const rows = publications.map((pub) => {
    const groupAuthors = (pub.group_author_ids || [])
      .map((id) => state.peopleById.get(id)?.name || id)
      .join("; ");
    return [
      pub.title || "",
      pub.year || "",
      pub.citation_count || 0,
      pub.citation_count_since_year ?? "",
      pub.venue || "",
      (pub.author_names || []).filter((name) => name !== "...").join("; "),
      groupAuthors,
      pub.doi || "",
      pub.url || pub.doi_url || pub.openalex_url || "",
      pub.google_scholar_cites_url || "",
    ];
  });
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n") + "\n";
}

function asciiToken(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[^\x00-\x7F]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "");
}

function bibtexTitleTokens(title) {
  const stopWords = new Set(["a", "an", "and", "for", "from", "in", "of", "on", "or", "the", "to", "using", "with"]);
  return String(title || "")
    .normalize("NFKD")
    .match(/[A-Za-z0-9]+/g)
    ?.map(asciiToken)
    .filter((token) => token && !stopWords.has(token.toLowerCase()))
    .slice(0, 3) || [];
}

function bibtexKey(pub, usedKeys) {
  const authors = (pub.author_names || []).filter((name) => name && name !== "...");
  const firstAuthor = authors[0] || "unknown";
  const lastName = asciiToken(firstAuthor.split(/\s+/).pop()) || "unknown";
  const year = pub.year || "nd";
  const titlePart = bibtexTitleTokens(pub.title)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1, 24))
    .join("");
  const base = `${lastName}${year}${titlePart}`;
  let key = base;
  let suffix = 2;
  while (usedKeys.has(key)) {
    key = `${base}_${suffix}`;
    suffix += 1;
  }
  usedKeys.add(key);
  return key;
}

function bibtexEntryType(pub) {
  const text = `${pub.type || ""} ${pub.venue || ""} ${pub.title || ""}`.toLowerCase();
  if (text.includes("patent") || text.includes("software")) return "misc";
  if (text.includes("conference") || text.includes("proceedings") || text.includes("workshop") || text.includes("symposium")) {
    return "inproceedings";
  }
  if (text.includes("book")) return "book";
  return "article";
}

function bibtexEscape(value) {
  const replacements = {
    "\\": "\\textbackslash{}",
    "{": "\\{",
    "}": "\\}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
  };
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[\\{}&%$#_~^]/g, (char) => replacements[char]);
}

function publicationToBibtex(pub, usedKeys) {
  const type = bibtexEntryType(pub);
  const key = bibtexKey(pub, usedKeys);
  const fields = [];
  const authors = (pub.author_names || []).filter((name) => name && name !== "...").join(" and ");
  const venue = pub.venue || "";
  const url = pub.doi_url || pub.url || pub.openalex_url || "";
  if (authors) fields.push(["author", authors]);
  if (pub.title) fields.push(["title", pub.title]);
  if (pub.year) fields.push(["year", String(pub.year)]);
  if (venue) fields.push([type === "inproceedings" ? "booktitle" : type === "misc" ? "howpublished" : "journal", venue]);
  if (pub.doi) fields.push(["doi", pub.doi]);
  if (url) fields.push(["url", url]);

  const lines = [`@${type}{${key},`];
  fields.forEach(([name, value], index) => {
    const comma = index < fields.length - 1 ? "," : "";
    lines.push(`  ${name} = {${bibtexEscape(value)}}${comma}`);
  });
  lines.push("}");
  return lines.join("\n");
}

function selectedBibtexContent(publications) {
  const usedKeys = new Set();
  return publications.map((pub) => publicationToBibtex(pub, usedKeys)).join("\n\n") + "\n";
}

function revokeExportObjectUrls() {
  for (const url of state.exportObjectUrls) {
    URL.revokeObjectURL(url);
  }
  state.exportObjectUrls = [];
}

function makeObjectUrl(content, mimeType) {
  const url = URL.createObjectURL(new Blob([content], { type: `${mimeType};charset=utf-8` }));
  state.exportObjectUrls.push(url);
  return url;
}

function updateExportLinks() {
  revokeExportObjectUrls();
  const publications = selectedPublications();
  const bibtexLink = document.querySelector('[data-export-format="bibtex"]');
  const csvLink = document.querySelector('[data-export-format="csv"]');
  if (!publications.length) {
    bibtexLink.href = "#";
    csvLink.href = "#";
    return;
  }

  bibtexLink.href = makeObjectUrl(selectedBibtexContent(publications), "application/x-bibtex");
  csvLink.href = makeObjectUrl(selectedCsvContent(publications), "text/csv");
}

function publicationRow(pub) {
  const authorLine = (pub.author_names || []).slice(0, 8).join(", ");
  const moreAuthors = (pub.author_names || []).length > 8 ? " et al." : "";
  const venue = [pub.venue, pub.doi ? `doi:${pub.doi}` : ""].filter(Boolean).join(", ");
  const href = safeUrl(pub.url || pub.doi_url || pub.openalex_url);
  const selected = state.selectedPublicationIds.has(pub.id);
  const title = href
    ? `<a class="publication-name" href="${href}" rel="noopener noreferrer">${escapeHtml(pub.title)}</a>`
    : `<span class="publication-name">${escapeHtml(pub.title)}</span>`;
  return `
    <tr class="${selected ? "is-selected" : ""}">
      <td class="checkbox-col">
        <input
          class="publication-select"
          type="checkbox"
          aria-label="Select publication"
          data-publication-id="${escapeHtml(pub.id)}"
          ${selected ? "checked" : ""}
        >
      </td>
      <td>
        <div class="publication-title-cell">
          ${title}
          <div class="publication-meta">${escapeHtml(authorLine)}${moreAuthors}</div>
          <div class="publication-meta">${escapeHtml(venue)}</div>
          ${groupAuthorTags(pub)}
        </div>
      </td>
      <td class="citation-col">${formatNumber(pub.citation_count)}</td>
      <td class="year-col">${pub.year || ""}</td>
    </tr>
  `;
}

function currentPublications() {
  const query = document.getElementById("search-input").value.trim().toLowerCase();
  const personId = document.getElementById("person-filter").value;
  const sort = document.getElementById("sort-select").value;

  const filtered = state.publications.filter((pub) => {
    if (personId && !(pub.group_author_ids || []).includes(personId)) return false;
    if (query && !textForSearch(pub).includes(query)) return false;
    return true;
  });

  filtered.sort((a, b) => {
    if (sort === "citations-desc") {
      return (b.citation_count || 0) - (a.citation_count || 0) || (b.year || 0) - (a.year || 0);
    }
    if (sort === "title-asc") {
      return (a.title || "").localeCompare(b.title || "");
    }
    return (b.year || 0) - (a.year || 0) || (b.citation_count || 0) - (a.citation_count || 0);
  });

  return filtered;
}

function renderSortState() {
  const sort = document.getElementById("sort-select").value;
  for (const header of document.querySelectorAll(".sortable-header")) {
    const button = header.querySelector("button[data-sort-value]");
    const active = button?.dataset.sortValue === sort;
    header.classList.toggle("is-active", active);
    header.setAttribute("aria-sort", active ? "descending" : "none");
  }
}

function visiblePublicationIds() {
  return currentPublications().map((pub) => pub.id);
}

function setSelectAllCheckboxState(checkbox, visibleIds) {
  if (!checkbox) return;
  const selectedVisibleCount = visibleIds.filter((id) => state.selectedPublicationIds.has(id)).length;
  checkbox.disabled = !visibleIds.length;
  checkbox.checked = visibleIds.length > 0 && selectedVisibleCount === visibleIds.length;
  checkbox.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visibleIds.length;
}

function closeExportMenu() {
  const menu = document.getElementById("export-menu");
  const button = document.getElementById("export-button");
  menu.hidden = true;
  button.setAttribute("aria-expanded", "false");
}

function renderSelectionControls() {
  const selectedCount = state.selectedPublicationIds.size;
  const visibleIds = visiblePublicationIds();
  const headerRow = document.getElementById("publication-header-row");
  const selectionRow = document.getElementById("selection-header-row");
  const count = document.getElementById("selection-count");
  headerRow.hidden = selectedCount > 0;
  selectionRow.hidden = selectedCount === 0;
  count.textContent = selectedCount ? `${formatNumber(selectedCount)} selected` : "";

  setSelectAllCheckboxState(document.getElementById("table-select-all"), visibleIds);
  setSelectAllCheckboxState(document.getElementById("selection-select-all"), visibleIds);

  if (!selectedCount) closeExportMenu();
}

function toggleVisibleSelection(checked) {
  for (const id of visiblePublicationIds()) {
    if (checked) {
      state.selectedPublicationIds.add(id);
    } else {
      state.selectedPublicationIds.delete(id);
    }
  }
  renderPublications();
}

function handlePublicationSelection(event) {
  const checkbox = event.target.closest(".publication-select");
  if (!checkbox) return;
  const id = checkbox.dataset.publicationId;
  if (!id) return;

  if (checkbox.checked) {
    state.selectedPublicationIds.add(id);
  } else {
    state.selectedPublicationIds.delete(id);
  }
  renderPublications();
}

function toggleExportMenu() {
  const menu = document.getElementById("export-menu");
  const button = document.getElementById("export-button");
  const open = menu.hidden;
  if (open) updateExportLinks();
  menu.hidden = !open;
  button.setAttribute("aria-expanded", open ? "true" : "false");
}

function renderPublications() {
  const tbody = document.getElementById("publications-body");
  const filtered = currentPublications();
  const reviewCounts = state.summary?.review_status_counts || {};
  const sourceNote =
    reviewCounts.trusted_orcid || reviewCounts.candidate_openalex
      ? `; ${formatNumber(reviewCounts.trusted_orcid || 0)} ORCID-confirmed, ` +
        `${formatNumber(reviewCounts.candidate_openalex || 0)} OpenAlex candidates`
      : "";
  document.getElementById("publication-status").textContent =
    filtered.length === state.publications.length
      ? `${formatNumber(filtered.length)} articles${sourceNote}`
      : `${formatNumber(filtered.length)} of ${formatNumber(state.publications.length)} articles${sourceNote}`;
  renderSortState();

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td class="empty-row" colspan="4">No publications match the current filters.</td></tr>';
    renderSelectionControls();
    return;
  }

  tbody.innerHTML = filtered.map(publicationRow).join("");
  renderSelectionControls();
}

function setSort(sort) {
  const sortSelect = document.getElementById("sort-select");
  sortSelect.value = sort;
  renderPublications();
}

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to load ${path}`);
  return response.json();
}

async function init() {
  try {
    const [summary, people, publicationsData] = await Promise.all([
      loadJson("data/summary.json"),
      loadJson("data/people.json"),
      loadJson("data/publications.json"),
    ]);

    state.summary = summary;
    state.people = people;
    state.peopleById = new Map(people.map((person) => [person.id, person]));
    state.publications = publicationsData.publications || [];

    renderSummary();
    renderPeople();
    renderPublications();

    document.getElementById("search-input").addEventListener("input", renderPublications);
    document.getElementById("person-filter").addEventListener("change", renderPublications);
    document.getElementById("sort-select").addEventListener("change", renderPublications);
    document.getElementById("publications-body").addEventListener("change", handlePublicationSelection);
    document.getElementById("table-select-all").addEventListener("change", (event) => {
      toggleVisibleSelection(event.target.checked);
    });
    document.getElementById("selection-select-all").addEventListener("change", (event) => {
      toggleVisibleSelection(event.target.checked);
    });
    document.getElementById("export-button").addEventListener("click", toggleExportMenu);
    document.getElementById("export-menu").addEventListener("click", (event) => {
      const link = event.target.closest("[data-export-format]");
      if (!link) return;
      if (!state.selectedPublicationIds.size) {
        event.preventDefault();
        return;
      }
      setTimeout(closeExportMenu, 0);
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".export-control")) closeExportMenu();
    });
    for (const button of document.querySelectorAll(".sortable-header button[data-sort-value]")) {
      button.addEventListener("click", () => setSort(button.dataset.sortValue));
    }
    document.getElementById("citation-modal-open").addEventListener("click", openCitationModal);
    document.getElementById("citation-modal-close").addEventListener("click", closeCitationModal);
    document.getElementById("citation-modal").addEventListener("click", (event) => {
      if (event.target === event.currentTarget) closeCitationModal();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !document.getElementById("citation-modal").hidden) {
        closeCitationModal();
      } else if (event.key === "Escape") {
        closeExportMenu();
      }
    });
  } catch (error) {
    document.getElementById("publication-status").textContent = error.message;
  }
}

init();
