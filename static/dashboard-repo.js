const repoFull = document.querySelector('meta[name="promptdrift-repo-full"]')?.getAttribute("content") || "";
window.__defaultFeaturedStoryline = null;
window.__featuredStoryline = null;
window.__expandedStoryline = null;
window.__storylineLoading = false;
window.__storylineError = "";

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

function renderRiskTags(tags = []) {
    return tags.map((tag) => `<span class="tag">${tag}</span>`).join("");
}

function renderMutedTags(tags = []) {
    return tags.map((tag) => `<span class="tag tag-muted">${tag}</span>`).join("");
}

function cueTone(cueKey) {
    if (cueKey === "latest_high_severity") {
        return "danger";
    }
    if (cueKey === "stale_baseline" || cueKey === "provenance_gaps") {
        return "warning";
    }
    if (cueKey === "mixed_direction") {
        return "accent";
    }
    return "neutral";
}

function cueGlyph(cueKey) {
    if (cueKey === "latest_high_severity") {
        return "High severity";
    }
    if (cueKey === "stale_baseline") {
        return "Aging baseline";
    }
    if (cueKey === "provenance_gaps") {
        return "Missing provenance";
    }
    if (cueKey === "mixed_direction") {
        return "Mixed signals";
    }
    return "Repeated pattern";
}

function priorityGlyph(priority) {
    if (priority === "review_now") {
        return "⚑";
    }
    if (priority === "watch") {
        return "◉";
    }
    return "◆";
}

function renderProvenance(provenance, fallback = "No PR or history provenance yet") {
    if (!provenance) {
        return fallback;
    }
    const parts = [provenance.label, provenance.source_ref, provenance.review_context].filter(Boolean);
    return parts.length ? parts.join(" · ") : fallback;
}

function renderProfileMetric(label, baselineValue, currentValue) {
    return `
        <div class="profile-metric profile-metric-compact">
            <div class="profile-label">${label}</div>
            <div class="profile-values"><span>${baselineValue.toFixed(2)}</span><span class="muted">→</span><span>${currentValue.toFixed(2)}</span></div>
        </div>
    `;
}

function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
}

function metricGlyph(label) {
    const normalized = label.toLowerCase();
    if (normalized.includes("review")) {
        return "⚑";
    }
    if (normalized.includes("artifact")) {
        return "◈";
    }
    if (normalized.includes("baseline")) {
        return "◆";
    }
    if (normalized.includes("history")) {
        return "◌";
    }
    if (normalized.includes("pr")) {
        return "↗";
    }
    return "●";
}

function renderMetricGlyphCard(label, value, detail) {
    return `
        <div class="metric-glyph-card">
            <div class="metric-glyph-badge">${metricGlyph(label)}</div>
            <div class="metric-glyph-copy">
                <div class="metric-glyph-label">${label}</div>
                <div class="metric-glyph-value">${value}</div>
                <div class="metric-glyph-detail">${detail}</div>
            </div>
        </div>
    `;
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function createRingMeter({ value = 0, label = "", tone = "accent", size = 124, stroke = 12, centerLabel = "" }) {
    const normalized = clamp(value, 0, 1);
    const radius = (size - stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const dash = circumference * normalized;
    return `
        <svg viewBox="0 0 ${size} ${size}" class="ring-meter ring-meter-${tone}" aria-label="${label}">
            <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" class="ring-meter-track"></circle>
            <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" class="ring-meter-value" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${circumference * 0.25}"></circle>
            <text x="50%" y="48%" text-anchor="middle" class="ring-meter-center">${centerLabel}</text>
            <text x="50%" y="63%" text-anchor="middle" class="ring-meter-caption">${label}</text>
        </svg>
    `;
}

function formatTimestamp(timestamp) {
    if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
        return null;
    }
    try {
        return new Date(timestamp * 1000).toLocaleString();
    } catch {
        return null;
    }
}

function renderBriefRows({ changeSummary, flagSummary, whereLabel, whereUrl }) {
    const rows = [
        changeSummary ? `<div class="brief-row"><span class="brief-label">What changed</span><span class="brief-copy">${changeSummary}</span></div>` : "",
        flagSummary ? `<div class="brief-row"><span class="brief-label">Why flagged</span><span class="brief-copy">${flagSummary}</span></div>` : "",
        whereLabel ? `<div class="brief-row"><span class="brief-label">Where</span><span class="brief-copy">${whereUrl ? `<a class="link" href="${whereUrl}" target="_blank" rel="noreferrer noopener">${whereLabel}</a>` : whereLabel}</span></div>` : "",
    ].filter(Boolean);
    if (!rows.length) {
        return "";
    }
    return `<div class="brief-panel">${rows.join("")}</div>`;
}

function renderOpenChangeLink(label, url, className = "cta-link") {
    if (!url) {
        return `<span class="${className}">${label}</span>`;
    }
    return `<a class="${className}" href="${url}" data-open-source-change="${url}" target="_blank" rel="noreferrer noopener">${label}</a>`;
}

function renderStorylineAction(label, artifactPath, className = "link") {
    return `<button type="button" class="${className}" data-open-storyline="${encodeURIComponent(artifactPath)}">${label}</button>`;
}

function renderInsightCard(item, designProfile, tone = "primary") {
    const combinedReasons = [...new Set([...(item.risk_reasons || []), ...((designProfile && designProfile.risk_tags) || [])])];
    const updatedAt = formatTimestamp(item.updated_at);
    const targetLabel = item.review_target || "repo detail";
    const supportingTargetLabel = item.supporting_review_target || null;
    const supportingTargetMarkup = supportingTargetLabel
        ? (item.supporting_review_url
            ? `<a class="link" href="${item.supporting_review_url}" data-open-source-change="${item.supporting_review_url}" target="_blank" rel="noreferrer noopener">${supportingTargetLabel}</a>`
            : supportingTargetLabel)
        : "";
    const targetMarkup = item.review_url
        ? `<a class="link" href="${item.review_url}" data-open-source-change="${item.review_url}" target="_blank" rel="noreferrer noopener">${targetLabel}</a>`
        : targetLabel;
    const ctaMarkup = renderOpenChangeLink("Open source change →", item.review_url);
    const signalStrength = clamp(
        (item.priority === "review_now" ? 0.92 : item.priority === "watch" ? 0.64 : 0.36)
        + (combinedReasons.includes("critical surface") ? 0.08 : 0)
        + (combinedReasons.includes("guardrails weakened") ? 0.06 : 0),
        0.1,
        1,
    );
    return `
        <div class="triage-card ${tone === "secondary" ? "triage-card-secondary" : ""} ${tone === "featured" ? "triage-card-featured-case" : ""}">
            <div class="triage-card-header triage-card-header-visual">
                <div class="triage-card-header-main">
                    <div class="priority priority-${item.priority}">${priorityGlyph(item.priority)} ${item.priority.replace("_", " ")}</div>
                    <div class="insight-title">${item.title}</div>
                    <div class="triage-summary"><strong>${item.artifact_path}</strong> <span class="muted">(${item.artifact_type})</span></div>
                </div>
                <div class="triage-meter-wrap">
                    ${createRingMeter({ value: signalStrength, label: item.confidence_label, tone: item.priority === "review_now" ? "warning" : item.priority === "watch" ? "accent" : "success", centerLabel: "⚑" })}
                </div>
            </div>
            <div class="triage-card-body">
                <div class="tag-row">${renderRiskTags(combinedReasons)}</div>
                <div class="glance-strip">
                    <div class="glance-chip"><span class="glance-chip-label">Baseline</span><strong>${item.baseline_label}</strong></div>
                    <div class="glance-chip"><span class="glance-chip-label">Evidence</span><strong>${item.evidence_label || "baseline only"}</strong></div>
                    <div class="glance-chip"><span class="glance-chip-label">Source</span><strong>${targetMarkup}</strong></div>
                    ${supportingTargetLabel ? `<div class="glance-chip"><span class="glance-chip-label">Merged context</span><strong>${supportingTargetMarkup}</strong></div>` : ""}
                    ${updatedAt ? `<div class="glance-chip"><span class="glance-chip-label">Updated</span><strong>${updatedAt}</strong></div>` : ""}
                </div>
                ${renderBriefRows({
                    changeSummary: item.change_summary,
                    flagSummary: item.flag_summary,
                    whereLabel: targetLabel,
                    whereUrl: item.review_url,
                })}
                <details class="micro-detail">
                    <summary>Why this is here</summary>
                    <div class="micro-detail-body">
                        ${item.evidence_summary ? `<div class="meta-tight muted">${item.evidence_summary}</div>` : ""}
                        <div class="meta-tight muted">${item.rationale}</div>
                        <div class="meta-tight">${item.recommended_action}</div>
                    </div>
                </details>
            </div>
            <div class="triage-card-footer triage-card-footer-split">
                <span class="muted">Target: ${targetLabel}</span>
                <div class="triage-card-footer-actions">
                    ${renderStorylineAction("Open storyline", item.artifact_path, "cta-link cta-button")}
                    ${ctaMarkup}
                </div>
            </div>
        </div>
    `;
}

function renderFeaturedInsight(item) {
    if (!item) {
        return '<div class="muted">No primary review target yet. Once drift history or PR evidence exists, this case file will feature the strongest item here.</div>';
    }
    const designProfiles = Object.fromEntries(asArray(window.__designProfiles).map((entry) => [entry.artifact_path, entry]));
    return renderInsightCard(item, designProfiles[item.artifact_path], "featured");
}

function renderInsights(items = []) {
    if (!items.length) {
        return '<div class="muted">No additional repo-level review targets are waiting behind the featured case.</div>';
    }
    const designProfiles = Object.fromEntries(asArray(window.__designProfiles).map((item) => [item.artifact_path, item]));
    return `<div class="queue-list">${items
        .map(
            (item, index) => `
                <div class="queue-card-wrap">
                    <div class="queue-rank queue-rank-large">#${index + 2}</div>
                    ${renderInsightCard(item, designProfiles[item.artifact_path], "primary")}
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaces(items = []) {
    if (!items.length) {
        return '<div class="muted">No grouped control surfaces yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count || 0), 1);
    return `<div class="surface-orbit">${items
        .map(
            (item, index) => {
                const size = 72 + (item.artifact_count / maxArtifacts) * 54;
                const positions = [
                    { left: 10, top: 10 },
                    { left: 60, top: 8 },
                    { left: 36, top: 34 },
                    { left: 12, top: 62 },
                    { left: 64, top: 58 },
                    { left: 38, top: 78 },
                ];
                const pos = positions[index] || { left: 40, top: 40 };
                return `
                <div class="surface-node" style="left:${pos.left}%; top:${pos.top}%; width:${size}px; height:${size}px;">
                    <div class="surface-node-inner">
                        <strong>${item.label}</strong>
                        <span>${item.artifact_count} tracked</span>
                        <small>${item.high_confidence_count} high confidence</small>
                    </div>
                </div>
            `;
            }
        )
        .join("")}</div>`;
}

function renderLeaderboard(items = []) {
    if (!items.length) {
        return '<div class="muted">No pull-request drift samples have been recorded yet.</div>';
    }
    const maxDrift = Math.max(...items.map((item) => item.drift_magnitude || 0), 0.001);
    return `<div class="artifact-ribbon">${items
        .map(
            (item) => `
                <div class="ribbon-item">
                    <div class="ribbon-bar" style="height:${48 + ((item.drift_magnitude || 0) / maxDrift) * 88}px"></div>
                    <div class="ribbon-label">${item.artifact_path.split("/").pop()}</div>
                    <div class="ribbon-meta">${item.drift_magnitude.toFixed(3)}</div>
                </div>`
        )
        .join("")}</div>`;
}

function renderArtifactConstellation(items = []) {
    if (!items.length) {
        return '<div class="muted">No lower-confidence findings are competing for attention right now.</div>';
    }
    return `<div class="constellation-map">${items.slice(0, 6)
        .map((item, index) => {
            const positions = [
                { left: 18, top: 22 },
                { left: 66, top: 16 },
                { left: 42, top: 42 },
                { left: 20, top: 70 },
                { left: 72, top: 64 },
                { left: 48, top: 82 },
            ];
            const pos = positions[index] || { left: 50, top: 50 };
            return `
                <div class="constellation-node" style="left:${pos.left}%; top:${pos.top}%;">
                    <span class="constellation-dot"></span>
                    <div class="constellation-label">${item.artifact_path.split("/").pop()}</div>
                </div>
            `;
        })
        .join("")}</div>`;
}

function renderLowerConfidenceInsights(items = []) {
    if (!items.length) {
        return '<div class="muted">No lower-confidence findings are competing for attention right now.</div>';
    }
    const designProfiles = Object.fromEntries(asArray(window.__designProfiles).map((item) => [item.artifact_path, item]));
    return `
        ${renderArtifactConstellation(items)}
        <div class="stack compact-stack lower-confidence-list">
            ${items.slice(0, 4).map((item) => renderInsightCard(item, designProfiles[item.artifact_path], "secondary")).join("")}
        </div>
    `;
}

function renderEvidenceStream({ baselineVersionCount = 0, historicalVersions = 0 }) {
    const total = Math.max(baselineVersionCount + historicalVersions, 1);
    return `
        <div class="evidence-stream">
            <span class="evidence-segment evidence-segment-baseline" style="width:${(baselineVersionCount / total) * 100}%"></span>
            <span class="evidence-segment evidence-segment-history" style="width:${(historicalVersions / total) * 100}%"></span>
        </div>
    `;
}

function renderInsightSpectrum(insights = [], lowerConfidenceInsights = []) {
    const reviewNow = insights.filter((item) => item.priority === "review_now").length;
    const watch = insights.filter((item) => item.priority === "watch").length;
    const baseline = Math.max(insights.length - reviewNow - watch, 0);
    const lower = lowerConfidenceInsights.length;
    const total = Math.max(reviewNow + watch + baseline + lower, 1);
    return `
        <div class="spectrum-wrap">
            <div class="lane-spectrum lane-spectrum-quad">
                <span class="lane-segment lane-segment-review" style="width:${(reviewNow / total) * 100}%"></span>
                <span class="lane-segment lane-segment-watch" style="width:${(watch / total) * 100}%"></span>
                <span class="lane-segment lane-segment-baseline" style="width:${(baseline / total) * 100}%"></span>
                <span class="lane-segment lane-segment-lower" style="width:${(lower / total) * 100}%"></span>
            </div>
            <div class="lane-spectrum-legend lane-spectrum-legend-dense">
                <span><strong>${reviewNow}</strong> review now</span>
                <span><strong>${watch}</strong> watch</span>
                <span><strong>${baseline}</strong> baseline</span>
                <span><strong>${lower}</strong> lower confidence</span>
            </div>
        </div>
    `;
}

function renderRepoCommandDeck({ payload, insights = [], lowerConfidenceInsights = [], controlSurfaces = [], leaderboard = [], backfill = {}, driftSummary = {} }) {
    const featuredInsight = insights[0] || null;
    const heat = clamp(
        (featuredInsight?.priority === "review_now" ? 0.92 : featuredInsight?.priority === "watch" ? 0.64 : 0.38)
        + Math.min(leaderboard.length * 0.03, 0.12)
        + Math.min(controlSurfaces.length * 0.02, 0.1),
        0.12,
        1,
    );
    const highConfidence = controlSurfaces.reduce((sum, item) => sum + (item.high_confidence_count || 0), 0);
    const totalArtifacts = controlSurfaces.reduce((sum, item) => sum + (item.artifact_count || 0), 0);
    const coverageRatio = clamp(highConfidence / Math.max(totalArtifacts, 1), 0, 1);
    const maxDrift = clamp(asNumber(driftSummary.avg_semantic_distance), 0, 1);
    return `
        <div class="pulseboard-grid repo-pulse-grid">
            <div class="card pulse-panel pulse-panel-hero">
                <div class="section-kicker">Case heat</div>
                <h2>How hard this repo is pulling attention</h2>
                <div class="repo-pulse-hero">
                    ${createRingMeter({ value: heat, label: featuredInsight?.priority ? featuredInsight.priority.replace("_", " ") : "baseline", tone: featuredInsight?.priority === "review_now" ? "warning" : featuredInsight?.priority === "watch" ? "accent" : "success", centerLabel: featuredInsight ? "⚑" : "·" })}
                    <div class="repo-pulse-copy">
                        <div class="focus-summary">${featuredInsight?.title || "No dominant review target yet"}</div>
                        <div class="meta-tight muted">${featuredInsight?.artifact_path || payload.repo_full || repoFull}</div>
                    </div>
                </div>
            </div>
            <div class="card pulse-panel">
                <div class="section-kicker">Evidence mix</div>
                <h2>What the case is built from</h2>
                ${renderEvidenceStream({
                    baselineVersionCount: asNumber(payload.baseline_version_count),
                    historicalVersions: asNumber(backfill.total_historical_versions),
                })}
                <div class="lane-spectrum-legend lane-spectrum-legend-dense">
                    <span><strong>${asNumber(payload.baseline_version_count)}</strong> baselines</span>
                    <span><strong>${asNumber(backfill.total_historical_versions)}</strong> history</span>
                </div>
            </div>
            <div class="card pulse-panel">
                <div class="section-kicker">Review mix</div>
                <h2>How signals break down</h2>
                ${renderInsightSpectrum(insights, lowerConfidenceInsights)}
            </div>
            <div class="card pulse-panel">
                <div class="section-kicker">Coverage quality</div>
                <h2>How strong the repo map is</h2>
                <div class="repo-mini-meters">
                    ${createRingMeter({ value: coverageRatio, label: "Coverage", tone: "accent", size: 96, stroke: 10, centerLabel: `${Math.round(coverageRatio * 100)}%` })}
                    ${createRingMeter({ value: maxDrift, label: "Avg drift", tone: "violet", size: 96, stroke: 10, centerLabel: driftSummary.avg_semantic_distance ? asNumber(driftSummary.avg_semantic_distance).toFixed(2) : "0.00" })}
                </div>
            </div>
        </div>
    `;
}

function renderHistoryTimelines(items = []) {
    if (!items.length) {
        return '<div class="muted">No historical timeline yet. Backfill or PR profile data will populate this view.</div>';
    }
    const maxDrift = Math.max(
        ...items.flatMap((item) => asArray(item.points).map((point) => asNumber(point.drift_magnitude))),
        1
    );
    return `<div class="stack">${items
        .map(
            (item) => `
                <div class="timeline-card timeline-card-graphic">
                    <div class="timeline-header">
                        <div>
                            <div class="insight-title">${item.artifact_path}</div>
                            <div class="muted meta-tight">${item.artifact_type} · ${item.point_count} recorded points</div>
                        </div>
                        <div class="muted">Max drift ${item.max_drift_magnitude.toFixed(3)}</div>
                    </div>
                    <div class="timeline-cosmos">${asArray(item.points)
                        .map(
                            (point, index) => `
                                <div class="timeline-star" style="left:${8 + index * (80 / Math.max(item.points.length, 1))}%; bottom:${12 + (point.drift_magnitude / maxDrift) * 70}%;">
                                    <span></span>
                                    <div class="timeline-star-label">${point.label}</div>
                                </div>
                            `
                        )
                        .join("")}</div>
                </div>
            `
        )
        .join("")}</div>`;
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
    const provenanceLabel = episode.source_url ? (episode.source_ref || "Open provenance") : (episode.source_ref || "No provenance link");
    return `
        <div class="artifact-card storyline-episode-card">
            <div class="artifact-card-head">
                <div>
                    <strong>${episode.source_label}</strong>
                    <div class="artifact-card-type">${formatDateLabel(episode.episode_timestamp)} · ${episode.episode_type.replace(/_/g, " ")}</div>
                </div>
                <span class="tag tag-muted">${episode.severity}</span>
            </div>
            ${asArray(episode.top_attributes).length ? `<div class="tag-row">${renderMutedTags(episode.top_attributes)}</div>` : ""}
            <div class="artifact-card-reason">${episode.episode_summary}</div>
            <div class="storyline-episode-meta muted">
                <span>${episode.confidence || ""}</span>
                <span>${episode.source_url ? `<a class="link" href="${episode.source_url}" data-open-source-change="${episode.source_url}" target="_blank" rel="noreferrer noopener">${provenanceLabel}</a>` : provenanceLabel}</span>
            </div>
        </div>
    `;
}

function renderFeaturedStoryline(storyline, expandedStoryline = null, isLoading = false, errorMessage = "") {
    if (!storyline) {
        return '<div class="muted">No storyline yet. Once baseline-relative history accumulates, PromptDrift will summarize the path here.</div>';
    }
    const compactEpisodes = asArray(storyline.episodes).slice(0, 3);
    const fullStoryline = expandedStoryline || null;
    const showingAlternate = window.__defaultFeaturedStoryline && window.__defaultFeaturedStoryline.artifact_path !== storyline.artifact_path;
    return `
        <div class="stack compact-stack">
            <div class="brief-panel">
                <div class="brief-row"><span class="brief-label">Artifact</span><span class="brief-copy"><strong>${storyline.artifact_path}</strong> <span class="muted">(${storyline.artifact_type})</span></span></div>
                <div class="brief-row"><span class="brief-label">Story</span><span class="brief-copy">${storyline.summary}</span></div>
                <div class="brief-row"><span class="brief-label">Current posture</span><span class="brief-copy">${storyline.current_posture_label}</span></div>
            </div>
            ${showingAlternate ? `<div class="meta-tight muted">Showing storyline for a non-featured artifact. ${renderStorylineAction("Return to featured artifact", window.__defaultFeaturedStoryline.artifact_path, "link")}</div>` : ""}
            ${storyline.limited_history_note ? `<div class="info-strip">${storyline.limited_history_note}</div>` : ""}
            <div class="stack compact-stack">
                ${compactEpisodes.map((episode) => renderStorylineEpisodeCard(episode)).join("")}
            </div>
            ${asArray(storyline.episodes).length > compactEpisodes.length || fullStoryline ? `
                <div class="stack compact-stack">
                    <button type="button" class="baseline-action-button" data-load-storyline="${encodeURIComponent(storyline.artifact_path)}" ${isLoading ? "disabled" : ""}>
                        ${isLoading ? "Loading full episode history..." : fullStoryline ? "Refresh full episode history" : "Load full episode history"}
                    </button>
                    ${errorMessage ? `<div class="muted">${errorMessage}</div>` : ""}
                    ${fullStoryline ? `
                        <details class="micro-detail" open>
                            <summary>Full episode history</summary>
                            <div class="micro-detail-body attribute-summary-list">
                                <div class="meta-tight muted">Showing ${asArray(fullStoryline.episodes).length} episode markers for ${fullStoryline.artifact_path}.</div>
                                ${fullStoryline.limited_history_note ? `<div class="meta-tight muted">${fullStoryline.limited_history_note}</div>` : ""}
                                ${asArray(fullStoryline.episodes).map((episode) => renderStorylineEpisodeCard(episode)).join("")}
                            </div>
                        </details>
                    ` : ""}
                </div>
            ` : ""}
        </div>
    `;
}

async function loadFullStoryline(artifactPath) {
    const decodedArtifactPath = decodeURIComponent(artifactPath);
    window.__storylineLoading = true;
    window.__storylineError = "";
    setSectionHtml(
        "featured-storyline",
        renderFeaturedStoryline(window.__featuredStoryline, window.__expandedStoryline, true, "")
    );
    bindFeaturedStoryline();
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${encodeURIComponent(decodedArtifactPath)}/episodes`);
        if (!response.ok) {
            throw new Error(`Artifact storyline request failed with ${response.status}`);
        }
        const payload = await response.json();
        window.__featuredStoryline = payload.storyline || null;
        window.__expandedStoryline = payload.storyline || null;
    } catch (error) {
        window.__storylineError = error instanceof Error ? error.message : "Unable to load full episode history.";
    } finally {
        window.__storylineLoading = false;
        setSectionHtml(
            "featured-storyline",
            renderFeaturedStoryline(window.__featuredStoryline, window.__expandedStoryline, false, window.__storylineError)
        );
        bindFeaturedStoryline();
        bindOpenSourceChangeLinks(document.getElementById("featured-storyline") || document);
    }
}

function bindFeaturedStoryline() {
    const container = document.getElementById("featured-storyline");
    if (!container) {
        return;
    }
    container.querySelectorAll("[data-load-storyline]").forEach((button) => {
        if (button.dataset.boundLoadStoryline === "true") {
            return;
        }
        button.dataset.boundLoadStoryline = "true";
        button.addEventListener("click", async () => {
            const artifactPath = button.getAttribute("data-load-storyline");
            if (!artifactPath) {
                return;
            }
            await loadFullStoryline(artifactPath);
        });
    });
}

function bindStorylineActions(scope = document) {
    scope.querySelectorAll("[data-open-storyline]").forEach((button) => {
        if (button.dataset.boundOpenStoryline === "true") {
            return;
        }
        button.dataset.boundOpenStoryline = "true";
        button.addEventListener("click", async () => {
            const artifactPath = button.getAttribute("data-open-storyline");
            if (!artifactPath) {
                return;
            }
            await loadFullStoryline(artifactPath);
            const storylineCard = document.getElementById("featured-storyline");
            if (storylineCard) {
                storylineCard.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    });
}

function renderHistoryCues(items = []) {
    if (!items.length) {
        return '<div class="muted">No repo-level history cues yet.</div>';
    }
    return `<div class="stack compact-stack">${items.map((item) => `
        <div class="artifact-card cue-card cue-card-${cueTone(item.cue_key)}">
            <div class="artifact-card-head">
                <div>
                    <div class="cue-kicker">${cueGlyph(item.cue_key)}</div>
                    <strong>${item.label}</strong>
                </div>
                <span class="tag tag-muted">${asArray(item.artifact_paths).length} artifact(s)</span>
            </div>
            <div class="artifact-card-reason">${item.summary}</div>
            <div class="cue-actions">${asArray(item.artifact_paths)
                .map((artifactPath) => renderStorylineAction(artifactPath, artifactPath, "cue-action-button"))
                .join("")}</div>
        </div>
    `).join("")}</div>`;
}

const DESIGN_PROFILE_FIELDS = [
    { key: "guardrail_robustness", label: "Guardrails" },
    { key: "capability_risk", label: "Capability" },
    { key: "autonomy_level", label: "Autonomy" },
    { key: "stability_vs_creativity", label: "Model config" },
    { key: "governance_strength", label: "Governance" },
];

const ATTRIBUTE_SUMMARY = {
    guardrail_robustness: {
        stronger: "Guardrails strengthened slightly, suggesting clearer constraints or refusal behavior.",
        weaker: "Guardrails weakened, which can reduce safety boundaries or escalation discipline.",
    },
    capability_risk: {
        stronger: "Capability risk increased, suggesting broader authority or more operational reach.",
        weaker: "Capability risk eased slightly relative to baseline.",
    },
    autonomy_level: {
        stronger: "Autonomy increased, implying the system may act with less human intervention.",
        weaker: "Autonomy decreased, implying more control or supervision than the current baseline.",
    },
    stability_vs_creativity: {
        stronger: "The current model configuration is more stable and deterministic than the baseline.",
        weaker: "The current model configuration is more creative or variable than the baseline.",
    },
    governance_strength: {
        stronger: "Governance strengthened, suggesting clearer review, ownership, or approval expectations.",
        weaker: "Governance weakened, which can make approval, audit, or accountability weaker.",
    },
};

function clampUnit(value) {
    const number = typeof value === "number" && Number.isFinite(value) ? value : 0;
    return Math.max(0, Math.min(1, number));
}

function polarPoint(centerX, centerY, radius, index, total) {
    const angle = (-Math.PI / 2) + (index / total) * Math.PI * 2;
    return {
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius,
    };
}

function radarPolygonPoints(profile, radius, centerX, centerY) {
    return DESIGN_PROFILE_FIELDS.map((field, index) => {
        const point = polarPoint(centerX, centerY, radius * clampUnit(profile[field.key]), index, DESIGN_PROFILE_FIELDS.length);
        return `${point.x.toFixed(1)},${point.y.toFixed(1)}`;
    }).join(" ");
}

function driftLabel(field, baselineValue, currentValue) {
    const delta = currentValue - baselineValue;
    const direction = delta > 0 ? "increased" : delta < 0 ? "decreased" : "unchanged";
    return `${field.label}: ${baselineValue.toFixed(2)} → ${currentValue.toFixed(2)} (${direction} ${Math.abs(delta).toFixed(2)})`;
}

function sourceHref(provenance) {
    if (provenance?.source_url) {
        return provenance.source_url;
    }
    if (!provenance?.source_ref) {
        return null;
    }
    const prMatch = provenance.source_ref.match(/PR #(\d+)/i);
    if (prMatch) {
        return `https://github.com/${repoFull}/pull/${prMatch[1]}`;
    }
    const commitMatch = provenance.source_ref.match(/commit\s+([a-f0-9]+)/i);
    if (commitMatch) {
        return `https://github.com/${repoFull}/commit/${commitMatch[1]}`;
    }
    return null;
}

function attributeChangeSummary(item) {
    return DESIGN_PROFILE_FIELDS.map((field) => {
        const baselineValue = clampUnit(item.baseline_profile[field.key]);
        const currentValue = clampUnit(item.current_profile[field.key]);
        const delta = currentValue - baselineValue;
        const magnitude = Math.abs(delta);
        let summary = "No material change relative to baseline.";
        if (magnitude >= 0.02) {
            const messages = ATTRIBUTE_SUMMARY[field.key];
            summary = delta >= 0 ? messages.stronger : messages.weaker;
        }
        const impact = magnitude >= 0.12 ? "high" : magnitude >= 0.05 ? "medium" : "low";
        const state = magnitude < 0.02 ? "no_change" : "drift_detected";
        return {
            label: field.label,
            baselineValue,
            currentValue,
            delta,
            impact,
            state,
            summary,
        };
    });
}

function attributeFindingForLabel(item, label) {
    const field = DESIGN_PROFILE_FIELDS.find((entry) => entry.label === label);
    if (!field) {
        return null;
    }
    return asArray(item.attribute_findings || []).find((finding) => finding.attribute_key === field.key) || null;
}

function renderAttributeFindings(item, sourceLink) {
    const findings = asArray(item.attribute_findings || []);
    if (!findings.length) {
        return "";
    }
    return `
        <details class="micro-detail">
            <summary>Why PromptDrift thinks so</summary>
            <div class="micro-detail-body attribute-summary-list">
                ${findings.map((finding) => `
                    <div class="attribute-summary-card">
                        <div class="attribute-summary-header">
                            <strong>${finding.label}</strong>
                            <span class="pill pill-${Math.abs(finding.delta) >= 0.12 ? "high" : Math.abs(finding.delta) >= 0.05 ? "medium" : "low"}">${finding.direction}</span>
                        </div>
                        <div class="meta-tight muted">${finding.reason}</div>
                        ${asArray(finding.evidence).length ? `
                            <div class="meta-tight"><strong>Changed code:</strong></div>
                            <ul class="evidence-list">
                                ${asArray(finding.evidence).map((entry) => `<li>${entry}</li>`).join("")}
                            </ul>
                        ` : ""}
                        <div class="meta-tight">${finding.remediation}</div>
                        ${sourceLink ? `<div class="meta-tight">${renderOpenChangeLink("Open source change", sourceLink, "link")}</div>` : ""}
                    </div>
                `).join("")}
            </div>
        </details>
    `;
}

function renderBaselineControls(item) {
    if (!item.can_promote_source_to_baseline) {
        return `
            <div class="baseline-controls">
                <div class="meta-tight muted"><strong>Current baseline:</strong> ${item.baseline_provenance?.label || "No baseline"}</div>
                <div class="meta-tight muted">Store a PR or history source snapshot before promoting a new baseline.</div>
            </div>
        `;
    }
    return `
        <div class="baseline-controls">
            <div class="meta-tight muted"><strong>Current baseline:</strong> ${item.baseline_provenance?.label || "No baseline"}</div>
            <button type="button" class="baseline-action-button" data-promote-baseline="${encodeURIComponent(item.artifact_path)}">Use current source as baseline</button>
        </div>
    `;
}

function renderAttributeSummary(item) {
    const sourceLink = sourceHref(item.provenance);
    const normalizedProfile = asArray(item.attribute_profile || []);
    const changes = normalizedProfile.length
        ? normalizedProfile.map((dimension) => ({
            label: dimension.label,
            baselineValue: dimension.baseline_value,
            currentValue: dimension.current_value,
            direction: dimension.direction,
            impact: dimension.confidence_score >= 0.8 ? "high" : dimension.confidence_score >= 0.6 ? "medium" : "low",
            state: dimension.state,
            summary: dimension.reason,
            evidence: asArray(dimension.evidence || []),
            remediation: dimension.remediation || "",
            confidenceLabel: dimension.confidence_label,
        }))
        : attributeChangeSummary(item);
    return `
        <div class="stack compact-stack">
            ${normalizedProfile.length ? `
                <div class="tag-row">${normalizedProfile
                    .map((dimension) => `<span class="tag tag-muted">${dimension.label}: ${dimension.current_value}</span>`)
                    .join("")}</div>
            ` : ""}
            <div class="glance-strip">
                <div class="glance-chip"><span class="glance-chip-label">Baseline</span><strong>${item.baseline_provenance?.label || "No baseline"}</strong></div>
                <div class="glance-chip"><span class="glance-chip-label">Current source</span><strong>${renderProvenance(item.provenance, "Baseline only")}</strong></div>
                ${sourceLink ? `<div class="glance-chip"><span class="glance-chip-label">Open change</span><strong>${renderOpenChangeLink(item.provenance?.source_ref || "View source change", sourceLink, "link")}</strong></div>` : ""}
            </div>
            ${renderBaselineControls(item)}
            <div class="brief-panel">
                <div class="brief-row"><span class="brief-label">Summary</span><span class="brief-copy">${item.headline_summary || "Baseline-relative posture changed."}</span></div>
            </div>
            ${renderAttributeFindings(item, sourceLink)}
            <details class="micro-detail">
                <summary>Full posture comparison</summary>
                <div class="micro-detail-body attribute-summary-list">
                    ${changes.map((change) => `
                        <div class="attribute-summary-card">
                            <div class="attribute-summary-header">
                                <strong>${change.label}</strong>
                                <span class="pill ${change.state === "no_change" ? "pill-no-change" : change.state === "unknown" ? "pill-medium" : `pill-drift pill-${change.impact}`}">${change.state === "no_change" ? "no change" : change.state === "unknown" ? "unknown" : "drift detected"}</span>
                            </div>
                            <div class="meta-tight muted"><strong>${change.baselineValue}</strong> → <strong>${change.currentValue}</strong> · ${change.direction} · ${change.confidenceLabel}</div>
                            <div class="meta-tight muted">${change.summary}</div>
                        ${(() => {
                            const finding = attributeFindingForLabel(item, change.label);
                            if (normalizedProfile.length) {
                                const evidence = asArray(change.evidence || []);
                                return `
                            ${evidence.length ? `
                            <div class="meta-tight"><strong>What in the code changed:</strong></div>
                            <ul class="evidence-list">
                                ${evidence.map((entry) => `<li>${entry}</li>`).join("")}
                            </ul>
                            ` : ""}
                            <div class="meta-tight">${change.remediation || ""}</div>
                        `;
                            }
                            if (change.state === "no_change" || !finding) {
                                return "";
                            }
                            return `
                            <div class="meta-tight"><strong>What in the code changed:</strong></div>
                            <ul class="evidence-list">
                                ${asArray(finding.evidence || []).map((entry) => `<li>${entry}</li>`).join("")}
                            </ul>
                            <div class="meta-tight">${finding.reason || ""}</div>
                            <div class="meta-tight">${finding.remediation || ""}</div>
                        `;
                        })()}
                        ${change.state !== "no_change" && change.state !== "unknown" && sourceLink ? `<div class="meta-tight">${renderOpenChangeLink("Open relevant change", sourceLink, "link")}</div>` : ""}
                        </div>
                    `).join("")}
                </div>
            </details>
        </div>
    `;
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

async function promoteBaseline(artifactPath) {
    const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${artifactPath}/baseline`, {
        method: "POST",
    });
    if (!response.ok) {
        const message = `Baseline update failed with ${response.status}`;
        throw new Error(message);
    }
    await loadDashboard();
}

function renderRadarChart(item) {
    const centerX = 160;
    const centerY = 150;
    const radius = 98;
    const rings = [0.25, 0.5, 0.75, 1];
    const ringStroke = "rgba(157, 176, 208, 0.18)";
    const axisStroke = "rgba(157, 176, 208, 0.24)";
    const baselineStroke = "#4fd1a5";
    const baselineFill = "rgba(79, 209, 165, 0.28)";
    const currentStroke = "#78a6ff";
    const currentFill = "rgba(120, 166, 255, 0.26)";
    const axes = DESIGN_PROFILE_FIELDS.map((field, index) => {
        const outer = polarPoint(centerX, centerY, radius, index, DESIGN_PROFILE_FIELDS.length);
        const label = polarPoint(centerX, centerY, radius + 28, index, DESIGN_PROFILE_FIELDS.length);
        return `
            <line x1="${centerX}" y1="${centerY}" x2="${outer.x.toFixed(1)}" y2="${outer.y.toFixed(1)}" stroke="${axisStroke}" stroke-width="1" />
            <text x="${label.x.toFixed(1)}" y="${label.y.toFixed(1)}" fill="#9db0d0" font-size="11" text-anchor="middle">${field.label}</text>
        `;
    }).join("");

    const ringMarkup = rings.map((ring) => {
        const points = DESIGN_PROFILE_FIELDS.map((_, index) => {
            const point = polarPoint(centerX, centerY, radius * ring, index, DESIGN_PROFILE_FIELDS.length);
            return `${point.x.toFixed(1)},${point.y.toFixed(1)}`;
        }).join(" ");
        return `<polygon points="${points}" fill="none" stroke="${ringStroke}" stroke-width="1" />`;
    }).join("");

    return `
        <svg viewBox="0 0 320 300" class="radar-chart" role="img" aria-label="Baseline versus current posture radar chart">
            ${ringMarkup}
            ${axes}
            <polygon points="${radarPolygonPoints(item.baseline_profile, radius, centerX, centerY)}" fill="${baselineFill}" stroke="${baselineStroke}" stroke-width="2.5" />
            <polygon points="${radarPolygonPoints(item.current_profile, radius, centerX, centerY)}" fill="${currentFill}" stroke="${currentStroke}" stroke-width="2.5" />
            ${DESIGN_PROFILE_FIELDS.map((field, index) => {
                const baselinePoint = polarPoint(centerX, centerY, radius * clampUnit(item.baseline_profile[field.key]), index, DESIGN_PROFILE_FIELDS.length);
                const currentPoint = polarPoint(centerX, centerY, radius * clampUnit(item.current_profile[field.key]), index, DESIGN_PROFILE_FIELDS.length);
                return `
                    <circle cx="${baselinePoint.x.toFixed(1)}" cy="${baselinePoint.y.toFixed(1)}" r="3.5" fill="${baselineStroke}"><title>Baseline · ${driftLabel(field, clampUnit(item.baseline_profile[field.key]), clampUnit(item.current_profile[field.key]))}</title></circle>
                    <circle cx="${currentPoint.x.toFixed(1)}" cy="${currentPoint.y.toFixed(1)}" r="3.5" fill="${currentStroke}"><title>Current · ${driftLabel(field, clampUnit(item.baseline_profile[field.key]), clampUnit(item.current_profile[field.key]))}</title></circle>
                `;
            }).join("")}
            <circle cx="${centerX}" cy="${centerY}" r="3" fill="rgba(237, 242, 255, 0.75)" />
        </svg>
    `;
}

function renderDesignProfileDetail(item) {
    return `
        <div class="posture-layout">
            <div class="radar-wrap">
                ${renderRadarChart(item)}
                <div class="radar-legend">
                    <span class="legend-item"><span class="legend-swatch legend-swatch-baseline"></span>Baseline</span>
                    <span class="legend-item"><span class="legend-swatch legend-swatch-current"></span>Current</span>
                </div>
            </div>
            <div class="posture-details stack compact-stack">
                <div>
                    <div class="insight-title">${item.artifact_path}</div>
                    <div class="muted meta-tight">${item.artifact_type} · <span class="pill pill-${item.drift_tone || "low"}">${item.drift_label || "small drift"}</span></div>
                </div>
                ${renderAttributeSummary(item)}
                <div class="tag-row">${renderRiskTags(item.risk_tags)}</div>
                <div class="meta-tight muted">${asArray(item.narrative).join(" ")}</div>
            </div>
        </div>
    `;
}

function bindDesignProfiles(items = []) {
    const select = document.getElementById("design-profile-select");
    const detail = document.getElementById("design-profile-detail");
    if (!select || !detail || !items.length) {
        return;
    }

    const renderSelected = () => {
        const selected = items.find((item) => item.artifact_path === select.value) || items[0];
        detail.innerHTML = renderDesignProfileDetail(selected);
        bindOpenSourceChangeLinks(detail);
        detail.querySelectorAll("[data-promote-baseline]").forEach((button) => {
            button.addEventListener("click", async () => {
                const encodedPath = button.getAttribute("data-promote-baseline");
                if (!encodedPath) {
                    return;
                }
                const originalText = button.textContent;
                button.disabled = true;
                button.textContent = "Updating baseline...";
                try {
                    await promoteBaseline(encodedPath);
                } catch (error) {
                    button.disabled = false;
                    button.textContent = originalText || "Use current source as baseline";
                    const message = error instanceof Error ? error.message : "Unable to update baseline";
                    window.alert(message);
                }
            });
        });
    };

    select.addEventListener("change", renderSelected);
    renderSelected();
}

function renderDesignProfiles(items = []) {
    if (!items.length) {
        return '<div class="muted">No design-profile comparisons yet. Onboarded baseline data is available, but no prioritized surfaces are ready for comparison.</div>';
    }
    return `
        <div class="design-explorer">
            <div class="explorer-toolbar">
                <label class="explorer-label" for="design-profile-select">Artifact</label>
                <select id="design-profile-select" class="explorer-select">
                    ${items.map((item) => `<option value="${item.artifact_path}">${item.artifact_path}</option>`).join("")}
                </select>
            </div>
            <div id="design-profile-detail"></div>
        </div>
    `;
}

function renderArtifacts(items = []) {
    if (!items.length) {
        return '<div class="muted">No onboarded artifacts were found for this repository yet.</div>';
    }
    const maxDrift = Math.max(...items.map((item) => Math.max(item.latest_historical_drift_magnitude, item.leaderboard_drift_magnitude)), 0.001);
    return `<div class="artifact-card-grid">${items
        .map((item) => {
            const latestDrift = Math.max(item.latest_historical_drift_magnitude, item.leaderboard_drift_magnitude);
            return `
                <div class="artifact-card">
                    <div class="artifact-card-head">
                        <div>
                            <strong>${item.artifact_path}</strong>
                            <div class="artifact-card-type">${item.artifact_type}</div>
                        </div>
                        <span class="tag tag-muted">${item.discovery_confidence.toFixed(2)}</span>
                    </div>
                    <div class="artifact-card-meter"><span style="width:${(latestDrift / maxDrift) * 100}%"></span></div>
                    <div class="artifact-card-stats">
                        <span>baseline ${item.baseline_line_count}</span>
                        <span>history ${item.historical_version_count}</span>
                        <span>profiles ${item.historical_profile_count}</span>
                    </div>
                    <div class="artifact-card-meta">latest drift ${latestDrift.toFixed(3)}</div>
                    <div class="artifact-card-reason">${item.discovery_reason}</div>
                    <div class="meta-tight">${renderStorylineAction("Open storyline", item.artifact_path, "link")}</div>
                </div>
            `;
        })
        .join("")}</div>`;
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
        const driftSummary = payload.drift_summary || {};
        const designProfiles = asArray(payload.design_profiles);
        const insights = asArray(payload.insights);
        const lowerConfidenceInsights = asArray(payload.lower_confidence_insights);
        const controlSurfaces = asArray(payload.control_surface_groups);
        const leaderboard = asArray(payload.top_drifting_artifacts);
        const historyTimelines = asArray(payload.history_timelines);
        const featuredStoryline = payload.featured_storyline || null;
        const historyCues = asArray(payload.history_cues);
        const artifacts = asArray(payload.artifacts);
        window.__designProfiles = designProfiles;
        window.__defaultFeaturedStoryline = featuredStoryline;
        window.__featuredStoryline = featuredStoryline;
        window.__expandedStoryline = null;
        window.__storylineLoading = false;
        window.__storylineError = "";
        const featuredInsight = insights[0] || null;
        const remainingInsights = featuredInsight ? insights.slice(1) : insights;

        setSectionHtml(
            "summary",
            [
                renderMetricGlyphCard(
                    "Tracked artifacts",
                    onboarding ? onboarding.discovered_artifact_count : 0,
                    onboarding ? `Default branch: ${onboarding.default_branch}` : "No onboarding yet"
                ),
                renderMetricGlyphCard("Approved baselines", asNumber(payload.baseline_version_count), `Repo: ${payload.repo_full || repoFull}`),
                renderMetricGlyphCard(
                    "Needs review now",
                    insights.length,
                    `Lower confidence lane: ${lowerConfidenceInsights.length}`
                ),
                renderMetricGlyphCard(
                    "Historical versions",
                    asNumber(backfill.total_historical_versions),
                    `Historical profiles: ${asNumber(backfill.total_historical_profiles)}`
                ),
                renderMetricGlyphCard(
                    "PR audits (proposal)",
                    asNumber(payload.pull_request_audit_count),
                    "Proposal evidence is excluded from landed drift views."
                ),
                renderMetricGlyphCard(
                    "Avg semantic distance",
                    asNumber(driftSummary.avg_semantic_distance).toFixed(3),
                    `Highest capability artifact: ${driftSummary.highest_capability_artifact_path || "n/a"}`
                ),
            ].join("")
        );

        setSectionHtml("repo-command-deck", renderRepoCommandDeck({ payload, insights, lowerConfidenceInsights, controlSurfaces, leaderboard, backfill, driftSummary }));
        setSectionHtml("design-profiles", renderDesignProfiles(designProfiles));
        bindDesignProfiles(designProfiles);
        setSectionHtml("featured-insight", renderFeaturedInsight(featuredInsight));
        setSectionHtml("insights", renderInsights(remainingInsights));
        setSectionHtml("lower-confidence-insights", renderLowerConfidenceInsights(lowerConfidenceInsights));
        setSectionHtml("control-surfaces", renderControlSurfaces(controlSurfaces));
        setSectionHtml("featured-storyline", renderFeaturedStoryline(featuredStoryline, null, false, ""));
        setSectionHtml("history-cues", renderHistoryCues(historyCues));
        setSectionHtml("leaderboard", renderLeaderboard(leaderboard));
        setSectionHtml("history-timelines", renderHistoryTimelines(historyTimelines));
        setSectionHtml("artifacts", renderArtifacts(artifacts));
        bindFeaturedStoryline();
        bindStorylineActions(document);
        bindOpenSourceChangeLinks(document);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown repo dashboard error";
        const fallback = `<div class="muted">Unable to load repository dashboard. ${message}</div>`;
        setSectionHtml("repo-command-deck", fallback);
        setSectionHtml("summary", fallback);
        setSectionHtml("design-profiles", fallback);
        setSectionHtml("featured-insight", fallback);
        setSectionHtml("insights", fallback);
        setSectionHtml("lower-confidence-insights", fallback);
        setSectionHtml("control-surfaces", fallback);
        setSectionHtml("featured-storyline", fallback);
        setSectionHtml("history-cues", fallback);
        setSectionHtml("leaderboard", fallback);
        setSectionHtml("history-timelines", fallback);
        setSectionHtml("artifacts", fallback);
    }
}

loadDashboard();
