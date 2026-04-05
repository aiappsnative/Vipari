function asArray(value) {
    return Array.isArray(value) ? value : [];
}

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = html;
        element.classList.remove("loading-shell");
        element.classList.remove("muted");
    }
}

function bindSidebarNavigation() {
    document.querySelectorAll('.sidebar-nav-item[href^="#"]').forEach((link) => {
        if (link.dataset.boundNav === "true") {
            return;
        }
        link.dataset.boundNav = "true";
        link.addEventListener("click", (event) => {
            const href = link.getAttribute("href") || "";
            const targetId = href.slice(1);
            const scrollRoot = document.querySelector(".page-scroll");
            const target = targetId ? document.getElementById(targetId) : null;
            if (!scrollRoot || !target) {
                return;
            }
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            window.history.replaceState(null, "", `#${targetId}`);
        });
    });
}

function setText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
    }
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function severityForPriority(priority) {
    if (priority === "review_now") {
        return { label: "High", className: "severity-high" };
    }
    if (priority === "watch") {
        return { label: "Medium", className: "severity-medium" };
    }
    return { label: "Low", className: "severity-low" };
}

function detailButton() {
    return document.getElementById("detail-escalate-btn");
}

function detailAttributeProfile(item) {
    const riskItem = item._matchedRiskItem || null;
    return asArray(riskItem?.attribute_profile).filter((entry) => entry.attribute_key !== "control_surface_type");
}

function averageProfileValue(entries, keyPrefix) {
    if (!entries.length) {
        return 0;
    }
    const values = entries
        .map((entry) => Number(keyPrefix === "baseline" ? entry.baseline_value : entry.current_value))
        .filter((value) => Number.isFinite(value));
    if (!values.length) {
        return 0;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderDriftChips(repo) {
    const labels = [];
    const riskItem = repo._matchedRiskItem || null;
    const profile = detailAttributeProfile(repo);
    if (profile.length) {
        profile
            .filter((entry) => entry.state && entry.state !== "no_change")
            .slice(0, 3)
            .forEach((entry) => {
                const direction = String(entry.direction || "").toLowerCase();
                if (entry.attribute_key === "guardrail_robustness") {
                    labels.push({ text: direction === "weaker" ? "Guardrails weakened" : "Guardrails strengthened", className: "chip-guardrails" });
                } else if (entry.attribute_key === "capability_risk") {
                    labels.push({ text: direction === "stronger" ? "Capability expanded" : "Capability narrowed", className: "chip-capability" });
                } else if (entry.attribute_key === "autonomy_level") {
                    labels.push({ text: direction === "stronger" ? "Autonomy increased" : "Autonomy reduced", className: "chip-autonomy" });
                } else if (entry.attribute_key === "governance_strength") {
                    labels.push({ text: direction === "weaker" ? "Governance weakened" : "Governance strengthened", className: "chip-governance" });
                }
            });
    }
    if (!labels.length && repo.highest_baseline_label) {
        labels.push({ text: repo.highest_baseline_label, className: "chip-baseline" });
    }
    if (!labels.length && riskItem?.title) {
        labels.push({ text: riskItem.title, className: "chip-model" });
    }
    return labels;
}

function renderOverviewTriageRow(repo, index) {
    const severity = severityForPriority(repo.highest_priority);
    const chips = renderDriftChips(repo);
    const reason = repo.highest_change_summary || repo.highest_flag_summary || repo.highest_rationale || "PromptDrift found enough evidence here to make this repo the next review target.";
    const metaParts = [repo.highest_baseline_label, repo.highest_review_target, repo.highest_evidence_label].filter(Boolean);
    return `
        <div class="triage-row" data-severity="${severity.label.toLowerCase()}" data-row-index="${index}" role="button" tabindex="0">
            <div class="triage-row-top">
                <span class="severity-badge ${severity.className}">${severity.label}</span>
                <span class="artifact-name">${escapeHtml(repo.repo_full)} / ${escapeHtml(repo.highest_insight_artifact_path || "repo focus")}</span>
                <span class="triage-row-chevron" aria-hidden="true">→</span>
            </div>
            <div class="triage-row-chips">
                ${chips.map((chip) => `<span class="drift-chip ${chip.className}">${escapeHtml(chip.text)}</span>`).join("")}
            </div>
            <div class="triage-row-meta">${escapeHtml(metaParts.join(" · ") || "Repo-level drift posture")}</div>
            <div class="triage-row-reason">${escapeHtml(reason)}</div>
        </div>
    `;
}

function renderAttributeBars(entries) {
    if (!entries.length) {
        return '<div class="muted">No attribute-level drift profile is available for this selected repo yet.</div>';
    }
    return entries.map((entry) => {
        const baseline = clamp(Number(entry.baseline_value || 0), 0, 1);
        const current = clamp(Number(entry.current_value || 0), 0, 1);
        const direction = String(entry.direction || "").toLowerCase();
        const currentTone = direction === "weaker" ? "declined" : direction === "stronger" ? "expanded" : "stable";
        return `
            <div class="attr-row">
                <span class="attr-label">${escapeHtml(entry.label || entry.attribute_key || "Attribute")}</span>
                <div class="attr-bars">
                    <div class="attr-bar-track"><div class="attr-bar attr-bar-baseline" style="width:${baseline * 100}%"></div></div>
                    <div class="attr-bar-track"><div class="attr-bar attr-bar-current attr-bar-${currentTone}" style="width:${current * 100}%"></div></div>
                </div>
                <div class="attr-counts">
                    <span class="attr-count-baseline">${baseline.toFixed(2)}</span>
                    <span class="attr-arrow">→</span>
                    <span class="attr-count-current attr-count-${currentTone}">${current.toFixed(2)}</span>
                </div>
            </div>
        `;
    }).join("");
}

function setDetailScore(repo) {
    const profile = detailAttributeProfile(repo);
    const baselineScore = averageProfileValue(profile, "baseline");
    const currentScore = averageProfileValue(profile, "current");
    const delta = currentScore - baselineScore;

    setText("detail-baseline-score", Math.round(baselineScore * 100).toString());
    setText("detail-current-score", Math.round(currentScore * 100).toString());
    setText("detail-baseline-label", profile.length ? "Baseline posture" : "Baseline readiness");
    setText("detail-current-label", profile.length ? "Current posture" : "Current pressure");
    setText("detail-score-delta", `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)}`);

    const deltaElement = document.getElementById("detail-score-delta");
    if (deltaElement) {
        deltaElement.className = `score-delta ${delta > 0.02 ? "score-delta-up" : delta < -0.02 ? "score-delta-down" : "score-delta-flat"}`;
    }
}

function renderEvidenceList(repo) {
    const riskItem = repo._matchedRiskItem || null;
    const entries = [
        repo.highest_change_summary,
        repo.highest_flag_summary,
        riskItem?.evidence_summary,
        repo.highest_rationale,
    ].filter(Boolean);
    if (!entries.length) {
        return '<li>No detailed evidence summary is available yet.</li>';
    }
    return entries.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("");
}

function artifactPathForRepo(repo) {
    return repo.highest_insight_artifact_path || repo._matchedRiskItem?.artifact_path || "";
}

function repoDetailUrl(repo) {
    const url = new URL(`/dashboard/${encodeURIComponent(repo.repo_full)}`, window.location.origin);
    const artifactPath = artifactPathForRepo(repo);
    if (artifactPath) {
        url.searchParams.set("artifact", artifactPath);
    }
    return `${url.pathname}${url.search}`;
}

function applyOverviewDetail(repo) {
    const severity = severityForPriority(repo.highest_priority);
    const riskItem = repo._matchedRiskItem || null;
    const detailName = `${repo.repo_full} / ${artifactPathForRepo(repo) || "repo focus"}`;
    const subtitle = [repo.highest_insight_title, repo.highest_review_target, repo.highest_evidence_label].filter(Boolean).join(" · ") || "Selected repo posture";

    setText("detail-artifact-name", detailName);
    const badge = document.getElementById("detail-severity-badge");
    if (badge) {
        badge.textContent = severity.label;
        badge.className = `severity-badge ${severity.className}`;
    }
    setText("detail-subtitle", subtitle);
    setDetailScore(repo);
    setSectionHtml("detail-attributes", renderAttributeBars(detailAttributeProfile(repo)));
    setSectionHtml("detail-evidence-list", renderEvidenceList(repo));
    setText("detail-recommendation-body", repo.highest_recommended_action || repo.highest_flag_summary || "Inspect the selected repository case file before merge and confirm the changed control surface is still acceptable.");

    const button = detailButton();
    if (button) {
        button.disabled = false;
        button.onclick = () => {
            window.location.href = repoDetailUrl(repo);
        };
    }
}

function selectedOverviewRows() {
    return document.querySelectorAll(".triage-row");
}

function selectOverviewRow(row, items) {
    selectedOverviewRows().forEach((candidate) => candidate.classList.remove("selected"));
    row.classList.add("selected");
    const index = Number(row.getAttribute("data-row-index"));
    if (Number.isFinite(index) && items[index]) {
        applyOverviewDetail(items[index]);
    }
}

function bindOverviewRows(items) {
    selectedOverviewRows().forEach((row) => {
        const select = () => {
            selectOverviewRow(row, items);
        };
        const navigate = () => {
            const index = Number(row.getAttribute("data-row-index"));
            if (Number.isFinite(index) && items[index]) {
                window.location.href = repoDetailUrl(items[index]);
            }
        };
        row.addEventListener("mouseenter", select);
        row.addEventListener("focus", select);
        row.addEventListener("click", navigate);
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                navigate();
            }
            if (event.key === " ") {
                event.preventDefault();
                select();
            }
        });
    });
}

function autoSelectFirstTriageItem(items) {
    const firstRow = document.querySelector(".triage-row");
    if (firstRow) {
        selectOverviewRow(firstRow, items);
    }
}

function buildOverviewSelectionItems(attentionRepos = [], highestRiskItems = []) {
    return attentionRepos.map((repo) => ({
        ...repo,
        _matchedRiskItem: highestRiskItems.find((item) => item.repo_full === repo.repo_full) || null,
    }));
}

function filteredOverviewItems(items, filter) {
    if (filter === "high") {
        return items.filter((item) => item.highest_priority === "review_now");
    }
    if (filter === "medium") {
        return items.filter((item) => item.highest_priority === "watch");
    }
    return items;
}

function bindOverviewFilters(items) {
    document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.remove("active"));
            button.classList.add("active");
            renderOverviewQueue(items, button.getAttribute("data-filter") || "all");
        });
    });
}

function renderOverviewQueue(items, filter = "all") {
    const filtered = filteredOverviewItems(items, filter);
    setText("triage-count", `${filtered.length} item${filtered.length === 1 ? "" : "s"}`);
    setSectionHtml("triage-list", filtered.length ? filtered.map((item, index) => renderOverviewTriageRow(item, index)).join("") : '<div class="muted">No triage items match this filter.</div>');
    bindOverviewRows(filtered);
    autoSelectFirstTriageItem(filtered);
}

function renderDriftTypeBars(items = []) {
    if (!items.length) {
        return '<div class="muted">No control-surface drift distribution is available yet.</div>';
    }
    const maxCount = Math.max(...items.map((item) => Number(item.artifact_count || 0)), 1);
    return items.slice(0, 6).map((item) => `
        <div class="drift-type-row">
            <span class="drift-type-label">${escapeHtml(item.label)}</span>
            <div class="drift-type-track"><div class="drift-type-fill" style="width:${(Number(item.artifact_count || 0) / maxCount) * 100}%"></div></div>
            <span class="drift-type-count">${Number(item.artifact_count || 0)}</span>
        </div>
    `).join("");
}

function renderCoverageLegend(segments) {
    return segments.map((segment) => `
        <div class="coverage-legend-item">
            <span class="coverage-legend-dot" style="background:${segment.color}"></span>
            <span>${escapeHtml(segment.label)}</span>
            <strong>${segment.value}</strong>
        </div>
    `).join("");
}

function drawCoverageDonut(segments) {
    const canvas = document.getElementById("coverage-donut");
    if (!canvas) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }
    context.clearRect(0, 0, canvas.width, canvas.height);

    const total = Math.max(segments.reduce((sum, segment) => sum + segment.value, 0), 1);
    const center = canvas.width / 2;
    const radius = 38;
    const lineWidth = 14;
    let start = -Math.PI / 2;

    segments.forEach((segment) => {
        const sweep = (segment.value / total) * Math.PI * 2;
        context.beginPath();
        context.strokeStyle = segment.color;
        context.lineWidth = lineWidth;
        context.arc(center, center, radius, start, start + sweep);
        context.stroke();
        start += sweep;
    });

    context.beginPath();
    context.fillStyle = "#111210";
    context.arc(center, center, radius - lineWidth, 0, Math.PI * 2);
    context.fill();

    context.fillStyle = "#cdccca";
    context.font = "600 12px Inter";
    context.textAlign = "center";
    context.fillText(String(total), center, center + 4);
}

function populateCoverageSummary(attentionRepos = [], repos = []) {
    const approved = attentionRepos.filter((repo) => (repo.highest_baseline_label || "").includes("Approved")).length;
    const fallback = repos.filter((repo) => !(attentionRepos.find((candidate) => candidate.repo_full === repo.repo_full)?.highest_baseline_label || "").includes("Approved") && Number(repo.discovered_artifact_count || 0) > 0).length;
    const missing = Math.max(repos.length - approved - fallback, 0);
    const segments = [
        { label: "Approved", value: approved, color: "#4f98a3" },
        { label: "Fallback", value: fallback, color: "#5591c7" },
        { label: "Missing", value: missing, color: "#e05c5c" },
    ];
    drawCoverageDonut(segments);
    setSectionHtml("coverage-legend", renderCoverageLegend(segments));
    setText("coverage-note", `Critical surfaces covered: ${approved + fallback} of ${repos.length}`);
}

function renderReposTable(repos = [], attentionRepos = []) {
    if (!repos.length) {
        return '<tr><td colspan="5" class="muted">No onboarded repositories yet.</td></tr>';
    }
    const attentionByRepo = new Map(attentionRepos.map((item) => [item.repo_full, item]));
    return repos.map((repo) => {
        const attention = attentionByRepo.get(repo.repo_full);
        const openItems = Number(attention?.review_now_count || 0) + Number(attention?.watch_count || 0);
        const status = attention?.highest_priority === "review_now"
            ? { label: "Urgent", className: "severity-high" }
            : attention?.highest_priority === "watch"
                ? { label: "Review needed", className: "severity-medium" }
                : { label: "Stable", className: "severity-low" };
        return `
            <tr>
                <td><a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${escapeHtml(repo.repo_full)}</a></td>
                <td>${Number(repo.discovered_artifact_count || 0)}</td>
                <td>${openItems}</td>
                <td>${escapeHtml(repo.default_branch || "n/a")}</td>
                <td><span class="severity-badge ${status.className}">${status.label}</span></td>
            </tr>
        `;
    }).join("");
}

function populateOverviewStats(payload, attentionRepos, highestRiskItems, controlSurfaceRisk, repos) {
    const approvedBaselineRepos = attentionRepos.filter((repo) => (repo.highest_baseline_label || "").includes("Approved")).length;
    const governanceRisk = controlSurfaceRisk.find((item) => (item.label || "").toLowerCase().includes("governance"));
    setText("stat-needs-review", String(payload.risk_state?.review_now_repo_count || 0));
    setText("stat-high-risk", String(highestRiskItems.length));
    setText("stat-approved", String(approvedBaselineRepos));
    setText("stat-gaps", String(governanceRisk?.artifact_count || 0));
    setText("repos-count", `${repos.length} repo${repos.length === 1 ? "" : "s"}`);
}

async function loadOverview() {
    try {
        const response = await fetch("/api/dashboard/overview");
        if (!response.ok) {
            throw new Error(`Overview request failed with ${response.status}`);
        }

        const payload = await response.json();
        const regressionPatterns = asArray(payload.regression_patterns);
        const highestRiskItems = asArray(payload.highest_risk_items);
        const controlSurfaceRisk = asArray(payload.control_surface_risk);
        const attentionRepos = asArray(payload.attention_repos);
        const controlSurfaceCoverage = asArray(payload.control_surface_coverage);
        const repos = asArray(payload.repos);
        const selectionItems = buildOverviewSelectionItems(attentionRepos, highestRiskItems);

        populateOverviewStats(payload, attentionRepos, highestRiskItems, controlSurfaceRisk, repos);
        setSectionHtml("drift-type-bars", renderDriftTypeBars(controlSurfaceRisk));
        populateCoverageSummary(attentionRepos, repos);
        setSectionHtml("repos-tbody", renderReposTable(repos, attentionRepos));
        renderOverviewQueue(selectionItems, "all");
        bindOverviewFilters(selectionItems);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown overview error";
        const fallback = `<div class="muted">Unable to load dashboard overview. ${escapeHtml(message)}</div>`;
        setText("stat-needs-review", "-");
        setText("stat-high-risk", "-");
        setText("stat-approved", "-");
        setText("stat-gaps", "-");
        setText("triage-count", "Unavailable");
        setText("repos-count", "Unavailable");
        setSectionHtml("triage-list", fallback);
        setSectionHtml("detail-attributes", fallback);
        setSectionHtml("detail-evidence-list", `<li>${escapeHtml(message)}</li>`);
        setSectionHtml("drift-type-bars", fallback);
        setSectionHtml("coverage-legend", fallback);
        setSectionHtml("repos-tbody", `<tr><td colspan="5" class="muted">${escapeHtml(message)}</td></tr>`);
        setText("coverage-note", "Critical surfaces covered: unavailable");
        setText("detail-artifact-name", "Overview unavailable");
        setText("detail-subtitle", message);
        setText("detail-recommendation-body", message);
        const button = detailButton();
        if (button) {
            button.disabled = true;
        }
    }
}

bindSidebarNavigation();
loadOverview();
