const repoFull = document.querySelector('meta[name="promptdrift-repo-full"]')?.getAttribute("content") || "";
window.__storylineCache = new Map();
window.__selectedInsight = null;
window.__designProfiles = [];

function asArray(value) {
    return Array.isArray(value) ? value : [];
}

function asNumber(value, fallback = 0) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = html;
    }
}

function setText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
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

function findDesignProfile(artifactPath) {
    return asArray(window.__designProfiles).find((item) => item.artifact_path === artifactPath) || null;
}

function renderInsightChips(item, profile) {
    const reasons = [...new Set([...(item.risk_reasons || []), ...((profile?.risk_tags) || [])])];
    return reasons.slice(0, 4).map((reason) => {
        const normalized = String(reason).toLowerCase();
        const className = normalized.includes("guardrail")
            ? "chip-guardrails"
            : normalized.includes("capability")
                ? "chip-capability"
                : normalized.includes("autonomy")
                    ? "chip-autonomy"
                    : normalized.includes("governance")
                        ? "chip-governance"
                        : normalized.includes("baseline")
                            ? "chip-baseline"
                            : "chip-model";
        return `<span class="drift-chip ${className}">${escapeHtml(reason)}</span>`;
    }).join("");
}

function renderRepoTriageRow(item, index) {
    const severity = severityForPriority(item.priority);
    const profile = findDesignProfile(item.artifact_path);
    const meta = [item.baseline_label, item.review_target, item.evidence_label].filter(Boolean).join(" · ");
    return `
        <div class="triage-row" data-row-index="${index}" data-severity="${severity.label.toLowerCase()}" role="button" tabindex="0">
            <div class="triage-row-top">
                <span class="severity-badge ${severity.className}">${severity.label}</span>
                <span class="artifact-name">${escapeHtml(repoFull)} / ${escapeHtml(item.artifact_path)}</span>
                <span class="triage-row-chevron" aria-hidden="true">→</span>
            </div>
            <div class="triage-row-chips">${renderInsightChips(item, profile)}</div>
            <div class="triage-row-meta">${escapeHtml(meta || item.title)}</div>
            <div class="triage-row-reason">${escapeHtml(item.change_summary || item.flag_summary || item.rationale || item.title)}</div>
        </div>
    `;
}

function renderAttributeBars(profile) {
    const attributes = asArray(profile?.attribute_profile || []).filter((entry) => entry.attribute_key !== "control_surface_type");
    if (!attributes.length) {
        return '<div class="muted">No attribute-level baseline comparison is available for this artifact yet.</div>';
    }
    return attributes.map((entry) => {
        const baseline = clamp(Number(entry.baseline_value || 0), 0, 1);
        const current = clamp(Number(entry.current_value || 0), 0, 1);
        const direction = String(entry.direction || "").toLowerCase();
        const tone = direction === "weaker" ? "declined" : direction === "stronger" ? "expanded" : "stable";
        return `
            <div class="attr-row">
                <span class="attr-label">${escapeHtml(entry.label || entry.attribute_key || "Attribute")}</span>
                <div class="attr-bars">
                    <div class="attr-bar-track"><div class="attr-bar attr-bar-baseline" style="width:${baseline * 100}%"></div></div>
                    <div class="attr-bar-track"><div class="attr-bar attr-bar-current attr-bar-${tone}" style="width:${current * 100}%"></div></div>
                </div>
                <div class="attr-counts">
                    <span class="attr-count-baseline">${baseline.toFixed(2)}</span>
                    <span class="attr-arrow">→</span>
                    <span class="attr-count-current attr-count-${tone}">${current.toFixed(2)}</span>
                </div>
            </div>
        `;
    }).join("");
}

function averageProfileScore(profile, key) {
    const attributes = asArray(profile?.attribute_profile || []).filter((entry) => entry.attribute_key !== "control_surface_type");
    if (!attributes.length) {
        return 0;
    }
    const values = attributes
        .map((entry) => Number(key === "baseline" ? entry.baseline_value : entry.current_value))
        .filter((value) => Number.isFinite(value));
    if (!values.length) {
        return 0;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderEvidenceList(item, profile) {
    const evidence = [
        item.change_summary,
        item.flag_summary,
        item.evidence_summary,
        item.rationale,
        ...(profile?.narrative || []),
    ].filter(Boolean);
    if (!evidence.length) {
        return '<li>No detailed evidence summary is available yet.</li>';
    }
    return evidence.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("");
}

function sourceUrlForInsight(item, profile) {
    return item.review_url || profile?.provenance?.source_url || profile?.baseline_provenance?.source_url || "";
}

function applyRepoDetail(item) {
    const profile = findDesignProfile(item.artifact_path);
    const severity = severityForPriority(item.priority);
    const subtitle = [item.title, item.review_target, item.evidence_label].filter(Boolean).join(" · ") || item.artifact_path;
    const baselineScore = averageProfileScore(profile, "baseline");
    const currentScore = averageProfileScore(profile, "current");
    const delta = currentScore - baselineScore;

    window.__selectedInsight = item;

    setText("detail-artifact-name", `${repoFull} / ${item.artifact_path}`);
    const badge = document.getElementById("detail-severity-badge");
    if (badge) {
        badge.textContent = severity.label;
        badge.className = `severity-badge ${severity.className}`;
    }
    setText("detail-subtitle", subtitle);
    setText("detail-baseline-score", Math.round(baselineScore * 100).toString());
    setText("detail-current-score", Math.round(currentScore * 100).toString());
    setText("detail-baseline-label", "Baseline posture");
    setText("detail-current-label", "Current posture");
    setText("detail-score-delta", `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)}`);
    const deltaElement = document.getElementById("detail-score-delta");
    if (deltaElement) {
        deltaElement.className = `score-delta ${delta > 0.02 ? "score-delta-up" : delta < -0.02 ? "score-delta-down" : "score-delta-flat"}`;
    }

    setSectionHtml("detail-attributes", renderAttributeBars(profile));
    setSectionHtml("detail-evidence-list", renderEvidenceList(item, profile));
    setText("detail-recommendation-body", item.recommended_action || profile?.headline_summary || "Inspect the selected artifact before merge and confirm the changed control surface is still acceptable.");

    const button = detailButton();
    if (button) {
        const targetUrl = sourceUrlForInsight(item, profile);
        button.disabled = false;
        button.onclick = () => {
            if (targetUrl) {
                window.open(targetUrl, "_blank", "noopener,noreferrer");
                return;
            }
            window.location.href = `/dashboard/${encodeURIComponent(repoFull)}`;
        };
    }

    loadArtifactStoryline(item.artifact_path);
}

function bindRepoRows(items) {
    document.querySelectorAll(".triage-row").forEach((row) => {
        const activate = () => {
            document.querySelectorAll(".triage-row").forEach((candidate) => candidate.classList.remove("selected"));
            row.classList.add("selected");
            const index = Number(row.getAttribute("data-row-index"));
            if (Number.isFinite(index) && items[index]) {
                applyRepoDetail(items[index]);
            }
        };
        row.addEventListener("click", activate);
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activate();
            }
        });
    });
}

function autoSelectFirstRepoRow() {
    const first = document.querySelector(".triage-row");
    if (first) {
        first.click();
    }
}

function filteredRepoItems(items, filter) {
    if (filter === "high") {
        return items.filter((item) => item.priority === "review_now");
    }
    if (filter === "medium") {
        return items.filter((item) => item.priority === "watch");
    }
    return items;
}

function renderRepoQueue(items, filter = "all") {
    const filtered = filteredRepoItems(items, filter);
    setText("repo-triage-count", `${filtered.length} item${filtered.length === 1 ? "" : "s"}`);
    setSectionHtml("triage-list", filtered.length ? filtered.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No repo insights match this filter.</div>');
    bindRepoRows(filtered);
    autoSelectFirstRepoRow();
}

function bindRepoFilters(items) {
    document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.remove("active"));
            button.classList.add("active");
            renderRepoQueue(items, button.getAttribute("data-filter") || "all");
        });
    });
}

function formatDateLabel(timestamp) {
    if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
        return "Unknown date";
    }
    try {
        return new Date(timestamp * 1000).toLocaleDateString();
    } catch {
        return "Unknown date";
    }
}

function renderStorylineEpisodeCard(episode) {
    const provenance = episode.source_url
        ? `<a class="link" href="${episode.source_url}" data-open-source-change="${episode.source_url}" target="_blank" rel="noreferrer noopener">${escapeHtml(episode.source_ref || "Open provenance")}</a>`
        : escapeHtml(episode.source_ref || "No provenance link");
    return `
        <div class="artifact-card">
            <div class="artifact-card-head">
                <div>
                    <strong>${escapeHtml(episode.source_label)}</strong>
                    <div class="artifact-card-type">${escapeHtml(formatDateLabel(episode.episode_timestamp))} · ${escapeHtml(String(episode.episode_type || "mixed").replaceAll("_", " "))}</div>
                </div>
                <span class="tag tag-muted">${escapeHtml(episode.severity || "low")}</span>
            </div>
            ${asArray(episode.top_attributes).length ? `<div class="tag-row">${asArray(episode.top_attributes).map((item) => `<span class="tag tag-muted">${escapeHtml(item)}</span>`).join("")}</div>` : ""}
            <div class="artifact-card-reason">${escapeHtml(episode.episode_summary || "")}</div>
            <div class="storyline-episode-meta muted">
                <span>${escapeHtml(episode.confidence || "")}</span>
                <span>${provenance}</span>
            </div>
        </div>
    `;
}

function renderStoryline(storyline) {
    if (!storyline) {
        return '<div class="muted">No storyline is available for the selected artifact yet.</div>';
    }
    return `
        <div class="stack compact-stack">
            <div class="brief-panel">
                <div class="brief-row"><span class="brief-label">Artifact</span><span class="brief-copy"><strong>${escapeHtml(storyline.artifact_path)}</strong> <span class="muted">(${escapeHtml(storyline.artifact_type)})</span></span></div>
                <div class="brief-row"><span class="brief-label">Story</span><span class="brief-copy">${escapeHtml(storyline.summary || "")}</span></div>
                <div class="brief-row"><span class="brief-label">Posture</span><span class="brief-copy">${escapeHtml(storyline.current_posture_label || "")}</span></div>
            </div>
            ${storyline.limited_history_note ? `<div class="detail-note">${escapeHtml(storyline.limited_history_note)}</div>` : ""}
            <div class="stack compact-stack">${asArray(storyline.episodes).map((episode) => renderStorylineEpisodeCard(episode)).join("")}</div>
        </div>
    `;
}

async function fetchArtifactStoryline(artifactPath) {
    if (window.__storylineCache.has(artifactPath)) {
        return window.__storylineCache.get(artifactPath);
    }
    const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${encodeURIComponent(artifactPath)}/episodes`);
    if (!response.ok) {
        throw new Error(`Artifact storyline request failed with ${response.status}`);
    }
    const payload = await response.json();
    window.__storylineCache.set(artifactPath, payload.storyline || null);
    return payload.storyline || null;
}

async function loadArtifactStoryline(artifactPath) {
    setSectionHtml("featured-storyline", '<div class="muted">Loading selected artifact storyline...</div>');
    try {
        const storyline = await fetchArtifactStoryline(artifactPath);
        setSectionHtml("featured-storyline", renderStoryline(storyline));
        bindOpenSourceChangeLinks(document.getElementById("featured-storyline") || document);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to load storyline";
        setSectionHtml("featured-storyline", `<div class="muted">${escapeHtml(message)}</div>`);
    }
}

function renderControlSurfaces(items = []) {
    if (!items.length) {
        return '<div class="muted">No grouped control surfaces yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => Number(item.artifact_count || 0)), 1);
    return items.map((item) => `
        <div class="drift-type-row">
            <span class="drift-type-label">${escapeHtml(item.label)}</span>
            <div class="drift-type-track"><div class="drift-type-fill" style="width:${(Number(item.artifact_count || 0) / maxArtifacts) * 100}%"></div></div>
            <span class="drift-type-count">${Number(item.artifact_count || 0)}</span>
        </div>
    `).join("");
}

function renderCueCards(items = []) {
    if (!items.length) {
        return '<div class="muted">No repo-level history cues yet.</div>';
    }
    return `<div class="stack compact-stack">${items.map((item) => `
        <div class="artifact-card">
            <div class="artifact-card-head">
                <strong>${escapeHtml(item.label)}</strong>
                <span class="tag tag-muted">${asArray(item.artifact_paths).length}</span>
            </div>
            <div class="artifact-card-reason">${escapeHtml(item.summary)}</div>
            <div class="tag-row">${asArray(item.artifact_paths).map((artifactPath) => `<button type="button" class="cue-action-button" data-storyline-artifact="${encodeURIComponent(artifactPath)}">${escapeHtml(artifactPath)}</button>`).join("")}</div>
        </div>
    `).join("")}</div>`;
}

function bindCueCards() {
    document.querySelectorAll("[data-storyline-artifact]").forEach((button) => {
        button.addEventListener("click", () => {
            const artifactPath = button.getAttribute("data-storyline-artifact");
            if (artifactPath) {
                loadArtifactStoryline(decodeURIComponent(artifactPath));
            }
        });
    });
}

function renderArtifactTable(items = []) {
    if (!items.length) {
        return '<tr><td colspan="5" class="muted">No onboarded artifacts were found for this repository yet.</td></tr>';
    }
    return items.map((item) => `
        <tr>
            <td>${escapeHtml(item.artifact_path)}</td>
            <td>${escapeHtml(item.artifact_type)}</td>
            <td>${Number(item.historical_profile_count || 0)}</td>
            <td>${Math.max(Number(item.latest_historical_drift_magnitude || 0), Number(item.leaderboard_drift_magnitude || 0)).toFixed(3)}</td>
            <td><button type="button" class="cue-action-button" data-storyline-artifact="${encodeURIComponent(item.artifact_path)}">Open storyline</button></td>
        </tr>
    `).join("");
}

function bindOpenSourceChangeLinks(scope = document) {
    scope.querySelectorAll("[data-open-source-change]").forEach((link) => {
        if (link.dataset.boundOpenSourceChange === "true") {
            return;
        }
        link.dataset.boundOpenSourceChange = "true";
        link.addEventListener("click", (event) => {
            const url = link.getAttribute("data-open-source-change");
            if (!url) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            window.open(url, "_blank", "noopener,noreferrer");
        });
    });
}

async function loadDashboard() {
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`);
        if (!response.ok) {
            throw new Error(`Repo dashboard request failed with ${response.status}`);
        }

        const payload = await response.json();
        const onboarding = payload.onboarding || null;
        const backfill = payload.backfill || {};
        const insights = asArray(payload.insights);
        const lowerConfidenceInsights = asArray(payload.lower_confidence_insights);
        const controlSurfaces = asArray(payload.control_surface_groups);
        const historyCues = asArray(payload.history_cues);
        const artifacts = asArray(payload.artifacts);
        const historyTimelines = asArray(payload.history_timelines);
        window.__designProfiles = asArray(payload.design_profiles);

        setText("repo-stat-artifacts", String(onboarding ? onboarding.discovered_artifact_count : artifacts.length));
        setText("repo-stat-review", String(insights.length));
        setText("repo-stat-baselines", String(asNumber(payload.baseline_version_count)));
        setText("repo-stat-history", String(historyTimelines.reduce((sum, item) => sum + Number(item.point_count || 0), 0)));

        setSectionHtml("triage-list", insights.length ? insights.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No primary repo insights are available yet.</div>');
        bindRepoRows(insights);
        bindRepoFilters(insights);
        autoSelectFirstRepoRow();

        setSectionHtml("featured-storyline", '<div class="muted">Select an insight to load its storyline.</div>');
        setSectionHtml("control-surfaces", renderControlSurfaces(controlSurfaces));
        setSectionHtml("history-cues", renderCueCards(historyCues));
        setSectionHtml("lower-confidence-insights", lowerConfidenceInsights.length
            ? `<div class="stack compact-stack">${lowerConfidenceInsights.slice(0, 4).map((item) => `<div class="artifact-card"><strong>${escapeHtml(item.artifact_path)}</strong><div class="artifact-card-reason">${escapeHtml(item.title || item.rationale || item.flag_summary || "Lower-confidence lead")}</div></div>`).join("")}</div>`
            : '<div class="muted">No lower-confidence findings are competing for attention right now.</div>');
        setSectionHtml("artifacts-tbody", renderArtifactTable(artifacts));
        bindCueCards();
        bindOpenSourceChangeLinks(document);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown repo dashboard error";
        const fallback = `<div class="muted">Unable to load repository dashboard. ${escapeHtml(message)}</div>`;
        setText("repo-stat-artifacts", "-");
        setText("repo-stat-review", "-");
        setText("repo-stat-baselines", "-");
        setText("repo-stat-history", "-");
        setText("repo-triage-count", "Unavailable");
        setSectionHtml("triage-list", fallback);
        setSectionHtml("featured-storyline", fallback);
        setSectionHtml("detail-attributes", fallback);
        setSectionHtml("detail-evidence-list", `<li>${escapeHtml(message)}</li>`);
        setSectionHtml("control-surfaces", fallback);
        setSectionHtml("history-cues", fallback);
        setSectionHtml("lower-confidence-insights", fallback);
        setSectionHtml("artifacts-tbody", `<tr><td colspan="5" class="muted">${escapeHtml(message)}</td></tr>`);
        setText("detail-artifact-name", repoFull || "Repository unavailable");
        setText("detail-subtitle", message);
        setText("detail-recommendation-body", message);
        const button = detailButton();
        if (button) {
            button.disabled = true;
        }
    }
}

loadDashboard();
