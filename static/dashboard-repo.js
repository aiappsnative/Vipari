const repoFull = document.querySelector('meta[name="driftguard-repo-full"]')?.getAttribute("content") || "";
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

function attributeScore(entry, keyPrefix) {
    const scoreKey = keyPrefix === "baseline" ? "baseline_score" : "current_score";
    const score = Number(entry?.[scoreKey]);
    if (Number.isFinite(score)) {
        return clamp(score, 0, 1);
    }
    const fallbackKey = keyPrefix === "baseline" ? "baseline_value" : "current_value";
    const fallback = Number(entry?.[fallbackKey]);
    if (Number.isFinite(fallback)) {
        return clamp(fallback, 0, 1);
    }
    return 0;
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
        const baseline = attributeScore(entry, "baseline");
        const current = attributeScore(entry, "current");
        const direction = String(entry.direction || "").toLowerCase();
        const tone = ["weaker", "reduced", "decreased"].includes(direction)
            ? "declined"
            : ["stronger", "expanded", "increased"].includes(direction)
                ? "expanded"
                : "stable";
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
        .map((entry) => attributeScore(entry, key))
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

function requestedArtifactPath() {
    const params = new URLSearchParams(window.location.search);
    return params.get("artifact") || "";
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

function autoSelectRepoRow(items, preferredArtifactPath = "") {
    const rows = Array.from(document.querySelectorAll(".triage-row"));
    if (!rows.length) {
        return;
    }
    if (preferredArtifactPath) {
        const preferredIndex = items.findIndex((item) => item.artifact_path === preferredArtifactPath);
        if (preferredIndex >= 0 && rows[preferredIndex]) {
            rows[preferredIndex].click();
            return;
        }
    }
    rows[0].click();
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

function renderRepoQueue(items, filter = "all", preferredArtifactPath = "") {
    const filtered = filteredRepoItems(items, filter);
    setText("repo-triage-count", `${filtered.length} item${filtered.length === 1 ? "" : "s"}`);
    setSectionHtml("triage-list", filtered.length ? filtered.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No repo insights match this filter.</div>');
    bindRepoRows(filtered);
    autoSelectRepoRow(filtered, preferredArtifactPath);
}

function bindRepoFilters(items, preferredArtifactPath = "") {
    document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.remove("active"));
            button.classList.add("active");
            renderRepoQueue(items, button.getAttribute("data-filter") || "all", preferredArtifactPath);
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

function storylineRiskClass(episode) {
    if (episode?.episode_type === "baseline_milestone" || episode?.source_type === "baseline_promotion") {
        return "storyline-risk-baseline";
    }
    const severity = String(episode?.severity || "low").toLowerCase();
    if (severity === "high") {
        return "storyline-risk-high";
    }
    if (severity === "medium") {
        return "storyline-risk-medium";
    }
    return "storyline-risk-low";
}

function storylineRiskLabel(episode) {
    if (episode?.episode_type === "baseline_milestone" || episode?.source_type === "baseline_promotion") {
        return "Approved baseline";
    }
    const severity = String(episode?.severity || "low").toLowerCase();
    if (severity === "high") {
        return "High attention";
    }
    if (severity === "medium") {
        return "Medium attention";
    }
    return "Low attention";
}

function storylineEpisodeTypeLabel(episode) {
    if (episode?.episode_type === "baseline_milestone") {
        return "Baseline checkpoint";
    }
    if (episode?.episode_type === "current_posture") {
        return "Current posture";
    }
    return String(episode?.episode_type || "mixed").replaceAll("_", " ");
}

function renderStorylineEpisodeNode(episode, index) {
    const provenance = episode.source_url
        ? `<a class="link" href="${episode.source_url}" data-open-source-change="${episode.source_url}" target="_blank" rel="noreferrer noopener">${escapeHtml(episode.source_ref || "Open provenance")}</a>`
        : escapeHtml(episode.source_ref || "No provenance link");
    const riskClass = storylineRiskClass(episode);
    const laneClass = index % 2 === 0 ? "storyline-node-top" : "storyline-node-bottom";
    const attributeTags = asArray(episode.top_attributes).slice(0, 3);
    return `
        <article class="storyline-node ${laneClass} ${riskClass}">
            <div class="storyline-node-date">${escapeHtml(formatDateLabel(episode.episode_timestamp))}</div>
            <div class="storyline-node-rail">
                <span class="storyline-node-dot" aria-hidden="true"></span>
            </div>
            <div class="storyline-node-card">
                <div class="storyline-node-card-head">
                    <div>
                        <strong>${escapeHtml(episode.source_label)}</strong>
                        <div class="artifact-card-type">${escapeHtml(storylineEpisodeTypeLabel(episode))}</div>
                    </div>
                    <span class="storyline-risk-pill">${escapeHtml(storylineRiskLabel(episode))}</span>
                </div>
                ${attributeTags.length ? `<div class="tag-row">${attributeTags.map((item) => `<span class="tag tag-muted">${escapeHtml(item)}</span>`).join("")}</div>` : ""}
                <div class="artifact-card-reason">${escapeHtml(episode.episode_summary || "")}</div>
                <div class="storyline-node-footer muted">
                    <span>${escapeHtml(episode.confidence || "")}</span>
                    <span>${provenance}</span>
                </div>
            </div>
        </article>
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
            <div class="storyline-timeline-scroll">
                <div class="storyline-timeline">${asArray(storyline.episodes).map((episode, index) => renderStorylineEpisodeNode(episode, index)).join("")}</div>
            </div>
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

function journeyToneForRisk(riskLevel) {
    const normalized = String(riskLevel || "").toLowerCase();
    if (normalized === "high") {
        return "journey-tone-medium";
    }
    if (normalized === "medium") {
        return "journey-tone-gap";
    }
    return "journey-tone-low";
}

function severityClassForRisk(riskLevel) {
    const normalized = String(riskLevel || "").toLowerCase();
    if (normalized === "high") {
        return "severity-high";
    }
    if (normalized === "medium") {
        return "severity-medium";
    }
    return "severity-low";
}

function snapshotTypeLabel(snapshotType) {
    const normalized = String(snapshotType || "checkpoint").replaceAll("_", " ");
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatSigned(value, digits = 3) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return "0.000";
    }
    return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function renderJourneySummary(snapshots = []) {
    if (!snapshots.length) {
        return '<div class="muted">No repository checkpoints have been materialized yet.</div>';
    }
    const current = snapshots.find((item) => item.snapshot_type === "current") || snapshots.find((item) => item.snapshot_type === "branch_head") || snapshots[snapshots.length - 1];
    const baseline = snapshots.find((item) => item.snapshot_type === "baseline_approved") || null;
    const mergedCount = snapshots.filter((item) => item.snapshot_type === "merge").length;
    const historicalCount = snapshots.filter((item) => item.snapshot_type === "historical_commit").length;
    const branchHeadCount = snapshots.filter((item) => item.snapshot_type === "branch_head").length;
    const riskLevel = current?.risk_summary?.risk_level || "low";
    return `
        <div class="journey-strip">
            <div class="journey-node journey-tone-primary">
                <span class="journey-node-value">${snapshots.length}</span>
                <span class="journey-node-label">Snapshots</span>
                <span class="journey-node-caption">${mergedCount} merged, ${historicalCount} historical, ${branchHeadCount} live</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node journey-tone-gap">
                <span class="journey-node-value">${baseline ? "Yes" : "No"}</span>
                <span class="journey-node-label">Baseline</span>
                <span class="journey-node-caption">${escapeHtml(baseline?.source_ref || "No approved baseline")}</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node ${journeyToneForRisk(riskLevel)}">
                <span class="journey-node-value">${escapeHtml(String(riskLevel).toUpperCase())}</span>
                <span class="journey-node-label">Current risk</span>
                <span class="journey-node-caption">score ${asNumber(current?.risk_summary?.score).toFixed(3)}</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node journey-tone-primary">
                <span class="journey-node-value">${asNumber(current?.distance_from_baseline).toFixed(3)}</span>
                <span class="journey-node-label">Drift from baseline</span>
                <span class="journey-node-caption">${asNumber(current?.change_breakdown?.critical_surfaces_changed)} critical surfaces changed</span>
            </div>
        </div>
    `;
}

function renderJourneyTimelineCard(snapshot) {
    const source = snapshot.source_url
        ? `<a class="link" href="${snapshot.source_url}" data-open-source-change="${snapshot.source_url}" target="_blank" rel="noreferrer noopener">${escapeHtml(snapshot.source_ref || "Open checkpoint")}</a>`
        : escapeHtml(snapshot.source_ref || "Stored checkpoint");
    const labels = asArray(snapshot.change_labels).slice(0, 3);
    return `
        <div class="artifact-card journey-card">
            <div class="artifact-card-head">
                <div>
                    <strong>${escapeHtml(snapshotTypeLabel(snapshot.snapshot_type))}</strong>
                    <div class="artifact-card-type">${escapeHtml(formatDateLabel(snapshot.created_at))} · ${escapeHtml(snapshot.commit_sha || snapshot.snapshot_key)}</div>
                </div>
                <span class="severity-badge ${severityClassForRisk(snapshot.risk_summary?.risk_level)}">${escapeHtml(snapshot.risk_summary?.risk_level || "low")}</span>
            </div>
            <div class="journey-metrics-row">
                <span>drift ${asNumber(snapshot.distance_from_baseline).toFixed(3)}</span>
                <span>critical ${asNumber(snapshot.change_breakdown?.critical_surfaces_changed)}</span>
                <span>artifacts ${asNumber(snapshot.artifact_coverage?.artifact_count)}</span>
            </div>
            ${labels.length ? `<div class="tag-row">${labels.map((label) => `<span class="tag tag-muted">${escapeHtml(label)}</span>`).join("")}</div>` : ""}
            <div class="artifact-card-reason">${escapeHtml(snapshot.change_summary?.changed_artifact_count ? `${snapshot.change_summary.changed_artifact_count} changed, ${snapshot.change_summary.added_artifact_count} added, ${snapshot.change_summary.removed_artifact_count} removed.` : "No material artifact changes recorded for this checkpoint.")}</div>
            <div class="storyline-episode-meta muted">
                <span>${escapeHtml(snapshot.default_branch || "")}</span>
                <span>${source}</span>
            </div>
        </div>
    `;
}

function renderJourneyTimeline(snapshots = []) {
    if (!snapshots.length) {
        return '<div class="muted">No timeline is available yet.</div>';
    }
    const timeline = snapshots.slice(-6).map((snapshot) => renderJourneyTimelineCard(snapshot)).join("");
    return `<div class="stack compact-stack">${timeline}</div>`;
}

function renderJourneyCompare(comparison) {
    if (!comparison) {
        return '<div class="muted">Baseline and current snapshots are required before DriftGuard can render a repository-level comparison.</div>';
    }
    const deltas = Object.entries(comparison.vector_delta || {})
        .sort((left, right) => Math.abs(Number(right[1]) || 0) - Math.abs(Number(left[1]) || 0))
        .slice(0, 6);
    const labels = asArray(comparison.change_labels);
    return `
        <div class="stack compact-stack">
            <div class="journey-strip">
                <div class="journey-node journey-tone-gap">
                    <span class="journey-node-value">${escapeHtml(String(comparison.comparison_kind || "arbitrary").replaceAll("_", " "))}</span>
                    <span class="journey-node-label">Comparison</span>
                    <span class="journey-node-caption">${escapeHtml(comparison.left?.snapshot_key || "left")} → ${escapeHtml(comparison.right?.snapshot_key || "right")}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node journey-tone-primary">
                    <span class="journey-node-value">${formatSigned(comparison.drift_summary?.drift_delta)}</span>
                    <span class="journey-node-label">Drift delta</span>
                    <span class="journey-node-caption">current ${asNumber(comparison.drift_summary?.right_distance_from_baseline).toFixed(3)}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node ${journeyToneForRisk(comparison.risk_summary?.risk_level)}">
                    <span class="journey-node-value">${escapeHtml(String(comparison.risk_summary?.risk_level || "low").toUpperCase())}</span>
                    <span class="journey-node-label">Risk level</span>
                    <span class="journey-node-caption">score ${asNumber(comparison.risk_summary?.score).toFixed(3)}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node journey-tone-medium">
                    <span class="journey-node-value">${asNumber(comparison.change_breakdown?.critical_surfaces_changed)}</span>
                    <span class="journey-node-label">Critical surfaces</span>
                    <span class="journey-node-caption">${asNumber(comparison.change_breakdown?.changed_artifact_count)} changed artifacts</span>
                </div>
            </div>
            ${labels.length ? `<div class="tag-row">${labels.map((label) => `<span class="drift-chip chip-model">${escapeHtml(label)}</span>`).join("")}</div>` : ""}
            <div class="journey-compare-grid">
                ${deltas.map(([key, value]) => `
                    <div class="journey-compare-row">
                        <span class="journey-compare-label">${escapeHtml(key.replaceAll("_", " "))}</span>
                        <span class="journey-compare-value ${Number(value) > 0 ? "journey-compare-up" : Number(value) < 0 ? "journey-compare-down" : ""}">${formatSigned(value, 4)}</span>
                    </div>
                `).join("")}
            </div>
            <div class="detail-note">${escapeHtml(`${asNumber(comparison.change_breakdown?.added_artifact_count)} added, ${asNumber(comparison.change_breakdown?.removed_artifact_count)} removed, and ${asNumber(comparison.change_breakdown?.changed_artifact_count)} changed artifacts between the approved baseline and the current landed posture.`)}</div>
        </div>
    `;
}

async function loadDashboard() {
    try {
        const dashboardResponse = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`);
        if (!dashboardResponse.ok) {
            throw new Error(`Repo dashboard request failed with ${dashboardResponse.status}`);
        }

        const payload = await dashboardResponse.json();
        const onboarding = payload.onboarding || null;
        const backfill = payload.backfill || {};
        const insights = asArray(payload.insights);
        const lowerConfidenceInsights = asArray(payload.lower_confidence_insights);
        const controlSurfaces = asArray(payload.control_surface_groups);
        const historyCues = asArray(payload.history_cues);
        const artifacts = asArray(payload.artifacts);
        const historyTimelines = asArray(payload.history_timelines);
        const journeySnapshots = asArray(payload.journey_snapshots);
        const preferredArtifactPath = requestedArtifactPath();
        window.__designProfiles = asArray(payload.design_profiles);
        const comparison = payload.journey_comparison || null;

        setText("repo-stat-artifacts", String(onboarding ? onboarding.discovered_artifact_count : artifacts.length));
        setText("repo-stat-review", String(insights.length));
        setText("repo-stat-baselines", String(asNumber(payload.baseline_version_count)));
        setText("repo-stat-history", String(historyTimelines.reduce((sum, item) => sum + Number(item.point_count || 0), 0)));

        setSectionHtml("triage-list", insights.length ? insights.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No primary repo insights are available yet.</div>');
        bindRepoRows(insights);
        bindRepoFilters(insights, preferredArtifactPath);
        autoSelectRepoRow(insights, preferredArtifactPath);

        setSectionHtml("featured-storyline", '<div class="muted">Select an insight to load its storyline.</div>');
        setSectionHtml("control-surfaces", renderControlSurfaces(controlSurfaces));
        setSectionHtml("history-cues", renderCueCards(historyCues));
        setSectionHtml("repo-journey-summary", renderJourneySummary(journeySnapshots));
        setSectionHtml("repo-journey-timeline", renderJourneyTimeline(journeySnapshots));
        setSectionHtml("repo-journey-compare", renderJourneyCompare(comparison));
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
        setSectionHtml("repo-journey-summary", fallback);
        setSectionHtml("repo-journey-timeline", fallback);
        setSectionHtml("repo-journey-compare", fallback);
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

bindSidebarNavigation();
loadDashboard();
