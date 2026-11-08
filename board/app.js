/* A static catalog: one data file, no accounts, dependencies or tracking. */
"use strict";
const $ = id => document.getElementById(id);
const FACETS = ["families", "regions", "styles", "levels", "stacks", "lists", "sources"];
const PARAM = { families: "family", regions: "region", styles: "style", levels: "level", stacks: "stack", lists: "list", sources: "source" };
const DEFAULT_REGIONS = ["Germany", "United Kingdom", "Italy", "Remote: worldwide", "Remote: Europe"];
const state = { jobs: [], companies: {}, filtered: [], visible: 10, view: "company", query: "", company: "", days: "", salary: false, sort: "newest", descriptions: "", loaded: {} };
FACETS.forEach(facet => { state[facet] = new Set(); });
const atsNames = { ashby: "Ashby", greenhouse: "Greenhouse", workable: "Workable" };
const familyNames = { software_engineering: "Software engineering", engineering_management: "Engineering management", product_management: "Product management", research: "Research", data_ml_engineering: "Data & ML engineering", data_science: "Data science" };
const levelNames = { intern: "Intern or student", junior: "Junior", mid: "Mid", senior: "Senior", lead: "Lead", principal: "Staff or principal", manager: "Manager", executive: "Head, director or VP", unknown: "Not stated" };
const regionOrder = ["Germany", "United Kingdom", "Italy", "Rest of Europe", "Remote: worldwide", "Remote: Europe", "Remote: region not stated"];
const label = (facet, value) => facet === "sources" ? atsNames[value] || value : facet === "families" ? familyNames[value] || value : facet === "levels" ? levelNames[value] || value : value;
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const normalize = value => String(value || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ß/g, "ss");
const plural = (count, noun) => `${count.toLocaleString()} ${count === 1 ? noun : noun === "company" ? "companies" : noun + "s"}`;
const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const employmentNames = { FullTime: "Full time", PartTime: "Part time", Contract: "Contract", Intern: "Internship", Temporary: "Temporary" };
const palettes = [["#eeecfb", "#5145b8"], ["#ebedf6", "#56628b"], ["#f2ede3", "#806b46"], ["#eaeef1", "#526777"], ["#f1e9e7", "#805d55"], ["#edebf2", "#705f82"]];
const companyInfo = job => state.companies[job.company] || {};

function companyWebsite(job) {
  const website = companyInfo(job).website;
  if (!website) return null;
  const link = el("a", "company-website", new URL(website).hostname.replace(/^www\./, "") + " ↗");
  link.href = website;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function avatar(job) {
  const hash = [...job.company].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const node = el("span", "avatar", job.companyName.slice(0, 1).toUpperCase());
  node.style.setProperty("--avatar-bg", palettes[hash % palettes.length][0]);
  node.style.setProperty("--avatar-ink", palettes[hash % palettes.length][1]);
  node.setAttribute("aria-hidden", "true");
  if (companyInfo(job).logoUrl) {
    const logo = el("img");
    logo.src = companyInfo(job).logoUrl;
    logo.alt = "";
    logo.loading = "lazy";
    logo.decoding = "async";
    logo.referrerPolicy = "no-referrer";
    logo.addEventListener("load", () => logo.classList.add("loaded"), { once: true });
    logo.addEventListener("error", () => logo.remove(), { once: true });
    node.append(logo);
  }
  return node;
}

function restoreState() {
  const params = new URLSearchParams(location.search);
  state.query = params.get("q") || "";
  for (const facet of FACETS) state[facet] = new Set(params.getAll(PARAM[facet]));
  const regions = params.getAll("region");
  // No region in the address means the defaults; "any" means every region.
  state.regions = new Set(regions.length ? regions.filter(r => r !== "any") : DEFAULT_REGIONS);
  state.sources = new Set([...state.sources].filter(source => atsNames[source]));
  state.company = params.get("company") || "";
  state.days = ["7", "30", "90"].includes(params.get("days")) ? params.get("days") : "";
  state.salary = params.get("salary") === "1";
  state.view = params.get("view") === "roles" ? "roles" : "company";
  state.sort = ["company", "newest", "oldest", "most", "title"].includes(params.get("sort")) ? params.get("sort") : "newest";
  syncControls();
}

function syncControls() {
  $("search").value = state.query;
  $("company").value = state.company;
  $("posted").value = state.days;
  $("salary").checked = state.salary;
  $("sort").value = state.sort;
  document.querySelectorAll("input[data-facet]").forEach(input => { input.checked = state[input.dataset.facet].has(input.value); });
  $("view-company").setAttribute("aria-pressed", String(state.view === "company"));
  $("view-roles").setAttribute("aria-pressed", String(state.view === "roles"));
}

function sameSet(a, b) {
  return a.size === b.length && b.every(value => a.has(value));
}

function saveUrl() {
  const params = new URLSearchParams();
  if (state.query.trim()) params.set("q", state.query.trim());
  for (const facet of FACETS) {
    if (facet === "regions") continue;
    [...state[facet]].sort().forEach(value => params.append(PARAM[facet], value));
  }
  if (!sameSet(state.regions, DEFAULT_REGIONS)) {
    if (state.regions.size) [...state.regions].sort().forEach(value => params.append("region", value));
    else params.set("region", "any");
  }
  if (state.company) params.set("company", state.company);
  if (state.days) params.set("days", state.days);
  if (state.salary) params.set("salary", "1");
  if (state.view !== "company") params.set("view", state.view);
  if (state.sort !== "newest") params.set("sort", state.sort);
  const selected = $("job-dialog").open ? $("job-dialog").dataset.job : "";
  if (selected) params.set("job", selected);
  history.replaceState(null, "", location.pathname + (params.size ? "?" + params : "") + location.hash);
}

function addFacet(container, values, facet) {
  container.replaceChildren();
  for (const [value, count] of values) {
    const option = el("label", "filter-option");
    const text = el("span");
    const input = el("input");
    input.type = "checkbox";
    input.value = value;
    input.dataset.facet = facet;
    input.addEventListener("change", () => {
      state[facet][input.checked ? "add" : "delete"](value);
      update();
    });
    text.append(input, document.createTextNode(label(facet, value)));
    option.append(text, el("small", "", count.toLocaleString()));
    container.append(option);
  }
}

function counts(values) {
  const map = new Map();
  values.forEach(value => map.set(value, (map.get(value) || 0) + 1));
  return map;
}

function setUpFilters() {
  const ordered = (map, order) => order.filter(value => map.has(value)).map(value => [value, map.get(value)]);
  const families = counts(state.jobs.map(job => job.family));
  const regions = counts(state.jobs.flatMap(job => job.regions));
  const styles = counts(state.jobs.map(job => job.style));
  const levels = counts(state.jobs.map(job => job.level || "unknown"));
  const stacks = counts(state.jobs.flatMap(job => job.stack));
  const lists = counts(state.jobs.flatMap(job => job.lists));
  const sources = counts(state.jobs.map(job => job.source));
  addFacet($("family-options"), ordered(families, Object.keys(familyNames)), "families");
  addFacet($("region-options"), ordered(regions, regionOrder), "regions");
  addFacet($("style-options"), ordered(styles, ["Remote", "Hybrid", "Office", "Not stated"]), "styles");
  addFacet($("level-options"), ordered(levels, Object.keys(levelNames)), "levels");
  addFacet($("stack-options"), [...stacks].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 30), "stacks");
  addFacet($("list-options"), [...lists].sort(([a], [b]) => a.localeCompare(b)), "lists");
  $("list-filter").hidden = lists.size === 0;
  addFacet($("source-options"), ordered(sources, Object.keys(atsNames)), "sources");
  const companies = new Map(state.jobs.map(job => [job.company, job.companyName]));
  [...companies].sort(([, a], [, b]) => a.localeCompare(b)).forEach(([slug, name]) => {
    const option = el("option", "", name);
    option.value = slug;
    $("company").append(option);
  });
}

function matches(job, tokens, cutoff) {
  if (state.families.size && !state.families.has(job.family)) return false;
  if (state.regions.size && !job.regions.some(region => state.regions.has(region))) return false;
  if (state.styles.size && !state.styles.has(job.style)) return false;
  if (state.levels.size && !state.levels.has(job.level || "unknown")) return false;
  if (state.stacks.size && !job.stack.some(tag => state.stacks.has(tag))) return false;
  if (state.lists.size && !job.lists.some(list => state.lists.has(list))) return false;
  if (state.sources.size && !state.sources.has(job.source)) return false;
  if (state.company && job.company !== state.company) return false;
  if (state.salary && !job.salary) return false;
  if (cutoff && (!job._date || job._date < cutoff)) return false;
  return tokens.every(token => job._search.includes(token));
}

function compareDate(a, b, ascending = false) {
  if (!a._date || !b._date) return a._date ? -1 : b._date ? 1 : a.title.localeCompare(b.title);
  return (ascending ? a._date - b._date : b._date - a._date) || a.title.localeCompare(b.title);
}

function jobOrder(a, b) {
  if (state.sort === "title") return a.title.localeCompare(b.title) || a.companyName.localeCompare(b.companyName);
  if (state.sort === "oldest") return compareDate(a, b, true);
  if (state.sort === "newest") return compareDate(a, b);
  return a.companyName.localeCompare(b.companyName) || a.company.localeCompare(b.company) || compareDate(a, b);
}

function grouped(jobs) {
  const groups = new Map();
  jobs.forEach(job => {
    if (!groups.has(job.company)) groups.set(job.company, []);
    groups.get(job.company).push(job);
  });
  const list = [...groups.values()];
  list.forEach(group => group.sort(state.sort === "title" ? (a, b) => a.title.localeCompare(b.title) : (a, b) => compareDate(a, b, state.sort === "oldest")));
  return list.sort((a, b) => {
    let result = 0;
    if (state.sort === "most") result = b.length - a.length;
    if (state.sort === "newest" || state.sort === "oldest") result = compareDate(a[0], b[0], state.sort === "oldest");
    return result || a[0].companyName.localeCompare(b[0].companyName) || a[0].company.localeCompare(b[0].company);
  });
}

function renderChips() {
  const container = $("active-filters");
  container.replaceChildren();
  const chips = [];
  const add = (text, remove) => {
    const chip = el("button", "filter-chip", text);
    chip.type = "button";
    chip.setAttribute("aria-label", "Remove filter: " + text);
    chip.append(el("span", "", "×"));
    chip.addEventListener("click", () => { remove(); syncControls(); update(); });
    chips.push(chip);
  };
  if (state.query.trim()) add('“' + state.query.trim() + '”', () => { state.query = ""; });
  for (const facet of FACETS) state[facet].forEach(value => add((facet === "sources" ? "Listed on " : "") + label(facet, value), () => state[facet].delete(value)));
  if (state.company) add(state.jobs.find(job => job.company === state.company)?.companyName || state.company, () => { state.company = ""; });
  if (state.days) add("Last " + state.days + " days", () => { state.days = ""; });
  if (state.salary) add("Salary listed", () => { state.salary = false; });
  container.append(...chips);
  $("filter-count").textContent = chips.length ? String(chips.length) : "";
}

function jobRow(job, flat = false) {
  const article = el("article", flat ? "job-row role-card" : "job-row");
  if (flat) article.append(avatar(job));
  const main = el("div", "job-main");
  if (flat) main.append(el("p", "role-company", job.companyName));
  const heading = el(flat ? "h3" : "h4", "job-title");
  const button = el("button", "job-open", job.title);
  button.type = "button";
  button.dataset.index = String(job._index);
  heading.append(button);
  main.append(heading);
  const meta = el("div", "job-meta");
  meta.append(el("span", "", job.location || "Location not stated"), el("span", "meta-dot"), el("span", "work-tag " + job.style.toLowerCase().replace(/\s+/g, "-"), job.style));
  if (job.employment) meta.append(el("span", "meta-dot"), el("span", "", employmentNames[job.employment] || job.employment));
  meta.append(el("span", "meta-dot"), el("span", "source-tag", familyNames[job.family] || job.family));
  main.append(meta);
  if (job.salary) main.append(el("p", "job-salary", job.salary));
  const right = el("div", "job-right");
  if (job._date) {
    const time = el("time", "", shortDate.format(job._date));
    time.dateTime = job.published;
    time.title = "Published " + dateFormat.format(job._date);
    right.append(time);
  }
  const arrow = el("span", "job-arrow", "↗");
  arrow.setAttribute("aria-hidden", "true");
  right.append(arrow);
  article.append(main, right);
  return article;
}

function renderJobs() {
  const container = $("job-list"), fragment = document.createDocumentFragment();
  let shown = 0, total = 0;
  if (state.view === "company") {
    const groups = grouped(state.filtered);
    total = groups.length;
    for (const group of groups.slice(0, state.visible)) {
      const section = el("section", "company-group");
      const header = el("div", "company-heading");
      const name = el("div");
      const title = el("div", "company-title");
      title.append(el("h3", "", group[0].companyName));
      name.append(title);
      const places = [...new Set(group.map(job => job.location).filter(Boolean))].slice(0, 4).join(" · ");
      const context = el("div", "company-context");
      context.append(el("p", "company-subtitle", places));
      const website = companyWebsite(group[0]);
      if (website) context.append(website);
      name.append(context);
      header.append(avatar(group[0]), name, el("p", "", plural(group.length, "role")));
      section.append(header, ...group.map(job => jobRow(job)));
      fragment.append(section);
      shown += group.length;
    }
  } else {
    const jobs = [...state.filtered];
    if (state.sort === "most") {
      const byCompany = counts(jobs.map(job => job.company));
      jobs.sort((a, b) => byCompany.get(b.company) - byCompany.get(a.company) || jobOrder(a, b));
    } else jobs.sort(jobOrder);
    total = jobs.length;
    jobs.slice(0, state.visible).forEach(job => fragment.append(jobRow(job, true)));
    shown = Math.min(state.visible, total);
  }
  container.replaceChildren(fragment);
  container.setAttribute("aria-busy", "false");
  $("empty").hidden = state.filtered.length !== 0;
  $("load-more").hidden = state.visible >= total;
  $("load-more").replaceChildren(document.createTextNode(state.view === "company" ? "Show more companies " : "Show more roles "), el("span", "", "↓"));
  $("shown-count").textContent = shown ? `Showing ${shown.toLocaleString()} of ${plural(state.filtered.length, "role")}` : "";
}

function update(reset = true) {
  if (reset) state.visible = state.view === "company" ? 10 : 40;
  const tokens = [...normalize(state.query).matchAll(/"([^"]+)"|(\S+)/g)].map(match => match[1] || match[2]);
  const cutoff = state.days ? Date.now() - Number(state.days) * 86400000 : 0;
  state.filtered = state.jobs.filter(job => matches(job, tokens, cutoff));
  const count = new Set(state.filtered.map(job => job.company)).size;
  $("result-count").textContent = `${plural(state.filtered.length, "role")} at ${plural(count, "company")}`;
  renderChips();
  renderJobs();
  saveUrl();
}

function resetFilters() {
  Object.assign(state, { query: "", company: "", days: "", salary: false });
  FACETS.forEach(facet => { state[facet] = new Set(); });
  state.regions = new Set(DEFAULT_REGIONS);
  syncControls();
  update();
}

function openJob(job) {
  const container = $("job-detail");
  const company = el("div", "detail-company");
  company.append(avatar(job), document.createTextNode(job.companyName));
  const title = el("h2", "", job.title);
  title.id = "detail-title";
  const tags = el("div", "detail-tags");
  [job.location, job.style, familyNames[job.family], levelNames[job.level], employmentNames[job.employment] || job.employment, job.department, "Listed on " + (atsNames[job.source] || job.source)].filter(Boolean).forEach(text => tags.append(el("span", "detail-tag", text)));
  const actions = el("div", "detail-actions");
  const link = el("a", "primary-button", "View & apply ↗");
  link.href = job.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  actions.append(link);
  container.replaceChildren(company, title, tags);
  const website = companyWebsite(job);
  if (website) container.append(website);
  if (job.salary) container.append(el("p", "detail-salary", job.salary));
  if (job.regions.length) container.append(el("p", "detail-locations", "Where: " + job.regions.join(" · ")));
  if (job.locations.length > 1) container.append(el("p", "detail-locations", "Listed locations: " + job.locations.join(" · ")));
  if (job.stack.length) container.append(el("p", "detail-locations", "Stack mentioned: " + job.stack.join(", ")));
  if (job.lists.length) container.append(el("p", "detail-locations", "On lists: " + job.lists.join(", ")));
  const description = el("div", "detail-description", "Loading the description…");
  container.append(actions, el("h3", "detail-heading", "About the role"), description);
  loadDescription(job).then(text => { description.textContent = text || "This company hasn’t provided a description here. Open the original posting for details."; });
  if (job._date) container.append(el("p", "detail-date", "Published " + dateFormat.format(job._date)));
  $("job-dialog").dataset.job = job.id;
  if (!$("job-dialog").open) $("job-dialog").showModal();
  $("job-dialog").scrollTop = 0;
  saveUrl();
}

async function loadDescription(job) {
  // Descriptions live in 256 files keyed by a hash of the job id, fetched on first use.
  try {
    if (!state.loaded[job.d]) {
      const response = await fetch(state.descriptions + job.d + ".json");
      if (!response.ok) throw new Error(response.status);
      state.loaded[job.d] = await response.json();
    }
    return state.loaded[job.d][job.id] || "";
  } catch (error) {
    console.error(error);
    return "The description couldn’t load. Open the original posting for details.";
  }
}

function bindEvents() {
  let timer;
  $("search").addEventListener("input", () => {
    clearTimeout(timer);
    state.query = $("search").value;
    timer = setTimeout(() => update(), 120);
  });
  [["salary", "salary"]].forEach(([id, key]) => $(id).addEventListener("change", () => { state[key] = $(id).checked; update(); }));
  [["company", "company"], ["posted", "days"], ["sort", "sort"]].forEach(([id, key]) => $(id).addEventListener("change", () => { state[key] = $(id).value; update(); }));
  ["company", "roles"].forEach(view => $("view-" + view).addEventListener("click", () => { state.view = view; syncControls(); update(); }));
  $("reset").addEventListener("click", resetFilters);
  $("clear-empty").addEventListener("click", () => { resetFilters(); state.regions = new Set(); syncControls(); update(); });
  $("load-more").addEventListener("click", () => { state.visible += state.view === "company" ? 10 : 40; renderJobs(); });
  $("job-list").addEventListener("click", event => {
    const button = event.target.closest("button[data-index]");
    if (button) openJob(state.jobs[Number(button.dataset.index)]);
  });
  $("close-job").addEventListener("click", () => $("job-dialog").close());
  $("job-dialog").addEventListener("close", saveUrl);
  $("mobile-filters").addEventListener("click", () => {
    $("mobile-filter-content").append($("filter-panel"));
    $("filter-dialog").showModal();
  });
  ["close-filters", "apply-filters"].forEach(id => $(id).addEventListener("click", () => $("filter-dialog").close()));
  $("filter-dialog").addEventListener("close", () => $("filter-home").append($("filter-panel")));
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest("input,textarea,select,[contenteditable]") && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      $("search").focus();
    }
  });
  window.addEventListener("popstate", () => { restoreState(); update(); });
}

async function init() {
  try {
    const response = await fetch(document.body.dataset.source);
    if (!response.ok) throw new Error("Could not load the collection");
    const data = await response.json();
    state.companies = data.companies || {};
    state.descriptions = data.descriptions || "";
    state.jobs = data.jobs.map((job, index) => ({ ...job, _index: index,
      _date: job.published ? Date.parse(job.published) || null : null,
      _search: normalize([job.title, job.companyName, job.company, job.location, ...job.locations, job.department, ...job.stack, familyNames[job.family]].join(" ")),
    }));
    $("hero-count").textContent = state.jobs.length.toLocaleString();
    $("hero-companies").textContent = new Set(state.jobs.map(job => job.company)).size.toLocaleString();
    const refreshed = new Date(data.generatedAt);
    $("updated").textContent = "Refreshed " + dateFormat.format(refreshed);
    $("updated").title = refreshed.toLocaleString();
    const selected = new URLSearchParams(location.search).get("job");
    setUpFilters();
    restoreState();
    bindEvents();
    update();
    $("search").disabled = false;
    if (selected) {
      const job = state.jobs.find(job => job.id === selected);
      if (job) openJob(job);
    }
  } catch (error) {
    $("job-list").replaceChildren(el("p", "loading-card", "The collection couldn’t load. Please refresh the page to try again."));
    $("job-list").setAttribute("aria-busy", "false");
    $("result-count").textContent = "Collection unavailable";
    $("updated").textContent = "Could not load jobs";
    console.error(error);
  }
}
init();
