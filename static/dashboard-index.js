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

const repoDashboardCache = new Map();
let overviewRepoPreviewState = {
    repoFull: null,
    activeRepoFull: null,
    lockedRepoFull: null,
    hoveredRepoFull: null,
    requestToken: 0,
    itemsByRepo: new Map(),
};

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
    const reason = repo.highest_change_summary || repo.highest_flag_summary || repo.highest_rationale || "DriftGuard found enough evidence here to make this repo the next review target.";
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

function normalizeScore(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    return clamp(numeric, 0, 1);
}

function averageNumeric(values) {
    const numeric = values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
    if (!numeric.length) {
        return 0;
    }
    return numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function averageAbsolute(values) {
    return averageNumeric(values.map((value) => Math.abs(Number(value) || 0)));
}

function repoCurrentProfile(payload) {
    const profiles = asArray(payload?.design_profiles);
    if (!profiles.length) {
        return null;
    }
    return {
        guardrails: normalizeScore(averageNumeric(profiles.map((profile) => profile?.current_profile?.guardrail_robustness))),
        capability: normalizeScore(averageNumeric(profiles.map((profile) => profile?.current_profile?.capability_risk))),
        autonomy: normalizeScore(averageNumeric(profiles.map((profile) => profile?.current_profile?.autonomy_level))),
        governance: normalizeScore(averageNumeric(profiles.map((profile) => profile?.current_profile?.governance_strength))),
        changeVelocity: normalizeScore(Math.min(1, Number(payload?.backfill?.total_historical_versions || 0) / 12)),
        criticality: normalizeScore(Math.min(1, averageAbsolute(asArray(payload?.artifacts).map((artifact) => artifact?.leaderboard_drift_magnitude || artifact?.latest_historical_drift_magnitude || 0)) * 1.6)),
    };
}

function repoBaselineProfile(payload) {
    const profiles = asArray(payload?.design_profiles);
    if (!profiles.length) {
        return null;
    }
    const current = repoCurrentProfile(payload);
    return {
        guardrails: normalizeScore(averageNumeric(profiles.map((profile) => profile?.baseline_profile?.guardrail_robustness))),
        capability: normalizeScore(averageNumeric(profiles.map((profile) => profile?.baseline_profile?.capability_risk))),
        autonomy: normalizeScore(averageNumeric(profiles.map((profile) => profile?.baseline_profile?.autonomy_level))),
        governance: normalizeScore(averageNumeric(profiles.map((profile) => profile?.baseline_profile?.governance_strength))),
        changeVelocity: normalizeScore(Math.max(0.08, (current?.changeVelocity || 0) * 0.35)),
        criticality: normalizeScore(Math.max(0.18, (current?.criticality || 0.25) * 0.82)),
    };
}

function repoPreviousProfile(payload) {
    const timeline = asArray(payload?.history_timelines)[0];
    if (!timeline || !asArray(timeline.points).length) {
        return null;
    }
    const latestPoint = asArray(timeline.points).slice(-1)[0];
    const current = repoCurrentProfile(payload);
    if (!latestPoint || !current) {
        return null;
    }
    return {
        guardrails: normalizeScore(current.guardrails + (Number(latestPoint.guardrail_shift || 0) * 0.5)),
        capability: normalizeScore(current.capability - (Number(latestPoint.capability_shift || 0) * 0.5)),
        autonomy: normalizeScore(current.autonomy - (Number(latestPoint.autonomy_shift || 0) * 0.5)),
        governance: normalizeScore(current.governance + (Number(latestPoint.guardrail_shift || 0) * 0.15)),
        changeVelocity: normalizeScore(Math.max(0.06, (current.changeVelocity || 0) * 0.7)),
        criticality: normalizeScore(Math.max(0.12, (current.criticality || 0) * 0.92)),
    };
}

function repoRadarVectors(payload) {
    const current = repoCurrentProfile(payload);
    const baseline = repoBaselineProfile(payload);
    if (!current || !baseline) {
        return null;
    }
    const previous = repoPreviousProfile(payload);
    const labels = ["Guardrails", "Capability", "Autonomy", "Governance", "Change velocity", "Criticality"];
    return {
        labels,
        series: [
            {
                label: "Approved baseline",
                color: "#4f98a3",
                fill: "rgba(79, 152, 163, 0.12)",
                values: [baseline.guardrails, baseline.capability, baseline.autonomy, baseline.governance, baseline.changeVelocity, baseline.criticality],
            },
            previous
                ? {
                    label: "Recent version",
                    color: "#5591c7",
                    fill: "rgba(85, 145, 199, 0.10)",
                    values: [previous.guardrails, previous.capability, previous.autonomy, previous.governance, previous.changeVelocity, previous.criticality],
                }
                : null,
            {
                label: "Current head",
                color: "#e0914a",
                fill: "rgba(224, 145, 74, 0.12)",
                values: [current.guardrails, current.capability, current.autonomy, current.governance, current.changeVelocity, current.criticality],
            },
        ].filter(Boolean),
    };
}

function drawRepoRadar(vectors) {
    const canvas = document.getElementById("repo-posture-radar");
    if (!canvas) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!vectors) {
        return;
    }

    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = Math.min(canvas.width, canvas.height) * 0.34;
    const count = vectors.labels.length;
    const angleStep = (Math.PI * 2) / count;

    context.strokeStyle = "rgba(255,255,255,0.08)";
    context.lineWidth = 1;
    for (let level = 1; level <= 4; level += 1) {
        const scale = level / 4;
        context.beginPath();
        vectors.labels.forEach((_, index) => {
            const angle = -Math.PI / 2 + (index * angleStep);
            const x = centerX + Math.cos(angle) * radius * scale;
            const y = centerY + Math.sin(angle) * radius * scale;
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.closePath();
        context.stroke();
    }

    vectors.labels.forEach((label, index) => {
        const angle = -Math.PI / 2 + (index * angleStep);
        const axisX = centerX + Math.cos(angle) * radius;
        const axisY = centerY + Math.sin(angle) * radius;
        context.beginPath();
        context.moveTo(centerX, centerY);
        context.lineTo(axisX, axisY);
        context.strokeStyle = "rgba(255,255,255,0.08)";
        context.stroke();

        const labelX = centerX + Math.cos(angle) * (radius + 20);
        const labelY = centerY + Math.sin(angle) * (radius + 20);
        context.fillStyle = "#797876";
        context.font = "500 11px Inter";
        context.textAlign = labelX >= centerX + 8 ? "left" : labelX <= centerX - 8 ? "right" : "center";
        context.textBaseline = labelY >= centerY + 8 ? "top" : labelY <= centerY - 8 ? "bottom" : "middle";
        context.fillText(label, labelX, labelY);
    });

    vectors.series.forEach((series) => {
        context.beginPath();
        series.values.forEach((value, index) => {
            const angle = -Math.PI / 2 + (index * angleStep);
            const x = centerX + Math.cos(angle) * radius * value;
            const y = centerY + Math.sin(angle) * radius * value;
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.closePath();
        context.fillStyle = series.fill;
        context.strokeStyle = series.color;
        context.lineWidth = 2;
        context.fill();
        context.stroke();
    });
}

function renderRepoRadarLegend(vectors) {
    if (!vectors) {
        return '<div class="muted">No repo posture vectors are available for this repo yet.</div>';
    }
    return vectors.series.map((series) => `
        <div class="radar-legend-item">
            <span class="coverage-legend-dot" style="background:${series.color}"></span>
            <span>${escapeHtml(series.label)}</span>
        </div>
    `).join("");
}

function latestActivityLabel(payload) {
    const timestamps = asArray(payload?.artifacts).map((artifact) => Number(artifact.latest_activity_at)).filter((value) => Number.isFinite(value) && value > 0);
    if (!timestamps.length) {
        return "Recent activity unavailable";
    }
    const latest = Math.max(...timestamps);
    const deltaHours = Math.max(Math.round((Date.now() / 1000 - latest) / 3600), 0);
    if (deltaHours < 1) {
        return "Updated within the last hour";
    }
    if (deltaHours < 24) {
        return `Updated ${deltaHours}h ago`;
    }
    const days = Math.round(deltaHours / 24);
    return `Updated ${days}d ago`;
}

function summarizeBaselineStatus(repo, payload = null) {
    if ((repo?.highest_baseline_label || "").includes("Approved") || Number(payload?.baseline_version_count || 0) > 0) {
        return "Approved";
    }
    if (Number(payload?.backfill?.total_historical_versions || 0) > 0) {
        return "Historical fallback";
    }
    return "No baseline";
}

function summarizeMonitoredSurfaces(payload) {
    const groups = asArray(payload?.control_surface_groups);
    if (!groups.length) {
        return "—";
    }
    return groups.slice(0, 2).map((group) => group.label).join(" · ");
}

function summarizeVersionCount(payload) {
    const versions = Number(payload?.backfill?.total_historical_versions || 0);
    return String(versions);
}

function summarizeDriftMagnitude(repo) {
    const drift = Number(repo?.top_drift_magnitude || 0);
    return drift.toFixed(2);
}

function renderJourneyPreview(payload) {
    const timelines = asArray(payload?.history_timelines);
    const topTimeline = timelines[0] || null;
    const points = asArray(topTimeline?.points);
    const latestPoint = points.slice(-1)[0] || null;
    const earliestPoint = points[0] || null;
    const milestones = [
        { label: "Baseline", value: `${Number(payload?.baseline_version_count || 0)}`, caption: "approved", tone: "primary" },
        earliestPoint ? { label: "First change", value: escapeHtml(earliestPoint.source_ref || earliestPoint.label || "history"), caption: "history", tone: "medium" } : null,
        latestPoint ? { label: "Recent change", value: escapeHtml(latestPoint.source_ref || latestPoint.label || "recent"), caption: `${Number(latestPoint.drift_magnitude || 0).toFixed(2)} drift`, tone: "gap" } : null,
        { label: "Current", value: `${Number(asArray(payload?.artifacts).length || 0)} surfaces`, caption: latestActivityLabel(payload), tone: "low" },
    ].filter(Boolean);
    return milestones.map((milestone, index) => `
        <div class="journey-node journey-tone-${escapeHtml(milestone.tone)}">
            <div class="journey-node-value journey-node-text">${milestone.value}</div>
            <div class="journey-node-label">${escapeHtml(milestone.label)}</div>
            <div class="journey-node-caption">${escapeHtml(milestone.caption || "")}</div>
            ${index < milestones.length - 1 ? '<div class="journey-node-link" aria-hidden="true"></div>' : ""}
        </div>
    `).join("");
}

function journeyPreviewNote(payload) {
    const historicalVersions = Number(payload?.backfill?.total_historical_versions || 0);
    const timelines = asArray(payload?.history_timelines);
    const topTimeline = timelines[0] || null;
    const timelineCount = timelines.length;
    if (!historicalVersions && !timelineCount) {
        return "Full repo version journey needs snapshot backend support; this preview shows what DriftGuard already tracks today.";
    }
    if (topTimeline) {
        return `${topTimeline.artifact_path} shows ${topTimeline.point_count} stored checkpoints; full repo journey still needs snapshot backend support.`;
    }
    return `${historicalVersions} historical artifact versions and ${timelineCount} tracked storyline timelines are currently available for preview.`;
}

function renderRepoRecentChanges(payload, repo) {
    const profile = asArray(payload?.design_profiles).find((item) => item.artifact_path === artifactPathForRepo(repo)) || asArray(payload?.design_profiles)[0];
    const groups = asArray(payload?.control_surface_groups).slice(0, 3).map((group) => group.label.toLowerCase());
    const findings = asArray(profile?.attribute_findings);
    const changes = [];
    if (profile?.headline_summary) {
        changes.push(profile.headline_summary.replace(/drift/gi, "change"));
    }
    findings.slice(0, 3).forEach((finding) => {
        changes.push(`${finding.label} ${finding.direction} (${Math.abs(Number(finding.delta || 0)).toFixed(2)}).`);
    });
    if (groups.length) {
        changes.push(`Most active control surfaces: ${groups.join(", ")}.`);
    }
    if (!changes.length) {
        changes.push("Recent repository changes are available, but no dominant neutral change summary has been isolated yet.");
    }
    return changes.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function renderRepoChangeBreakdown(payload, repo) {
    const profile = asArray(payload?.design_profiles).find((item) => item.artifact_path === artifactPathForRepo(repo)) || asArray(payload?.design_profiles)[0];
    const attributeProfile = asArray(profile?.attribute_profile);
    if (attributeProfile.length) {
        return renderAttributeBars(attributeProfile.filter((entry) => entry.attribute_key !== "control_surface_type"));
    }
    return renderAttributeBars(detailAttributeProfile(repo));
}

async function fetchRepoDashboard(repoFull) {
    if (!repoFull) {
        return null;
    }
    if (repoDashboardCache.has(repoFull)) {
        return repoDashboardCache.get(repoFull);
    }
    const request = fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`)
        .then(async (response) => {
            if (!response.ok) {
                throw new Error(`Repo dashboard request failed with ${response.status}`);
            }
            return response.json();
        })
        .catch((error) => {
            repoDashboardCache.delete(repoFull);
            throw error;
        });
    repoDashboardCache.set(repoFull, request);
    return request;
}

function selectRepoTableRow(repoFull) {
    document.querySelectorAll("[data-repo-row]").forEach((row) => {
        row.classList.toggle("selected", row.getAttribute("data-repo-row") === repoFull);
    });
}

function updateRepoPreviewHeader(repo, payload) {
    setText("repo-radar-title", `${repo.repo_full} posture`);
    setText("repo-radar-subtitle", `${summarizeBaselineStatus(repo, payload)} baseline · ${Number(payload?.baseline_version_count || 0)} approved checkpoints · ${asArray(payload?.artifacts).length} tracked surfaces`);
    setText("repo-radar-meta", latestActivityLabel(payload));
}

function hydrateRepoTableRow(repo, payload) {
    const row = document.querySelector(`[data-repo-row="${CSS.escape(repo.repo_full)}"]`);
    if (!row) {
        return;
    }
    const cells = row.querySelectorAll("td");
    if (cells.length < 7) {
        return;
    }
    cells[3].textContent = summarizeVersionCount(payload);
    cells[4].textContent = summarizeBaselineStatus(repo, payload);
    cells[5].textContent = latestActivityLabel(payload);
    cells[6].textContent = summarizeMonitoredSurfaces(payload);
}

async function enrichOverviewRepoDetail(repo, mode = "full") {
    const shell = document.getElementById("repo-radar-shell");
    if (shell) {
        shell.classList.add("loading-shell", "muted");
    }
    const requestToken = ++overviewRepoPreviewState.requestToken;
    overviewRepoPreviewState.repoFull = repo.repo_full;
    if (mode === "full") {
        overviewRepoPreviewState.activeRepoFull = repo.repo_full;
        overviewRepoPreviewState.lockedRepoFull = repo.repo_full;
        selectRepoTableRow(repo.repo_full);
    }
    if (mode === "preview") {
        overviewRepoPreviewState.hoveredRepoFull = repo.repo_full;
    }
    try {
        const payload = await fetchRepoDashboard(repo.repo_full);
        if (requestToken !== overviewRepoPreviewState.requestToken || overviewRepoPreviewState.repoFull !== repo.repo_full) {
            return;
        }
        const vectors = repoRadarVectors(payload);
        drawRepoRadar(vectors);
        setSectionHtml("repo-posture-legend", renderRepoRadarLegend(vectors));
        updateRepoPreviewHeader(repo, payload);
        setSectionHtml("repo-journey-strip", renderJourneyPreview(payload));
        setText("repo-journey-note", journeyPreviewNote(payload));
        hydrateRepoTableRow(repo, payload);
        if (mode === "full") {
            setSectionHtml("detail-attributes", renderRepoChangeBreakdown(payload, repo));
            setSectionHtml("detail-evidence-list", renderRepoRecentChanges(payload, repo));
        }
    } catch (error) {
        if (requestToken !== overviewRepoPreviewState.requestToken) {
            return;
        }
        const message = error instanceof Error ? error.message : "Unable to load repo posture preview.";
        setSectionHtml("repo-posture-legend", `<div class="muted">${escapeHtml(message)}</div>`);
        setSectionHtml("repo-journey-strip", `<div class="muted">${escapeHtml(message)}</div>`);
        setText("repo-journey-note", message);
        setText("repo-radar-meta", message);
        drawRepoRadar(null);
    } finally {
        if (requestToken === overviewRepoPreviewState.requestToken && shell) {
            shell.classList.remove("loading-shell", "muted");
        }
    }
}

function restoreRepoPreview() {
    const repoFull = overviewRepoPreviewState.lockedRepoFull || overviewRepoPreviewState.activeRepoFull;
    const item = repoFull ? overviewRepoPreviewState.itemsByRepo.get(repoFull) : null;
    if (!item) {
        return;
    }
    enrichOverviewRepoDetail(item, "preview");
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
    overviewRepoPreviewState.activeRepoFull = repo.repo_full;
    overviewRepoPreviewState.lockedRepoFull = repo.repo_full;
    selectRepoTableRow(repo.repo_full);
    setText("repo-radar-title", `${repo.repo_full} posture`);
    setText("repo-radar-subtitle", subtitle);
    setText("repo-radar-meta", "Loading repo posture preview...");
    setSectionHtml("repo-posture-legend", '<div class="muted">Loading posture comparison...</div>');
    setSectionHtml("repo-journey-strip", '<div class="muted">Loading repo journey preview...</div>');
    setText("repo-journey-note", "Loading current repo history preview...");
    enrichOverviewRepoDetail(repo, "full");

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
        return '<tr><td colspan="7" class="muted">No onboarded repositories yet.</td></tr>';
    }
    const attentionByRepo = new Map(attentionRepos.map((item) => [item.repo_full, item]));
    return repos.map((repo) => {
        const attention = attentionByRepo.get(repo.repo_full);
        const openItems = Number(attention?.review_now_count || 0) + Number(attention?.watch_count || 0);
        const scopeBadge = repo.dashboard_scope === "connected_history"
            ? '<span class="tag tag-muted repo-scope-badge">connected history</span>'
            : repo.allocation_status
                ? `<span class="tag repo-scope-badge repo-scope-badge-active">${escapeHtml(repo.allocation_status)}</span>`
                : "";
        return `
            <tr class="repos-table-row" data-repo-row="${escapeHtml(repo.repo_full)}" tabindex="0">
                <td><div class="repo-name-cell"><a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${escapeHtml(repo.repo_full)}</a>${scopeBadge}</div></td>
                <td>${openItems}</td>
                <td>${summarizeDriftMagnitude(attention)}</td>
                <td>—</td>
                <td>${escapeHtml(summarizeBaselineStatus(attention || repo))}</td>
                <td>Loading…</td>
                <td>Loading…</td>
            </tr>
        `;
    }).join("");
}

function bindRepoTablePreview(items) {
    const itemsByRepo = new Map(items.map((item) => [item.repo_full, item]));
    document.querySelectorAll("[data-repo-row]").forEach((row) => {
        const repoFull = row.getAttribute("data-repo-row") || "";
        const item = itemsByRepo.get(repoFull);
        if (!item || row.dataset.boundPreview === "true") {
            return;
        }
        row.dataset.boundPreview = "true";
        const preview = () => {
            overviewRepoPreviewState.hoveredRepoFull = repoFull;
            setText("repo-radar-title", `${repoFull} posture`);
            setText("repo-radar-subtitle", "Previewing repo posture from the repository table.");
            setText("repo-radar-meta", "Loading repo posture preview...");
            enrichOverviewRepoDetail(item, "preview");
        };
        row.addEventListener("mouseenter", preview);
        row.addEventListener("focus", preview);
        row.addEventListener("mouseleave", restoreRepoPreview);
        row.addEventListener("blur", restoreRepoPreview);
        row.addEventListener("click", (event) => {
            if (event.target instanceof Element && event.target.closest("a")) {
                return;
            }
            applyOverviewDetail(item);
        });
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                applyOverviewDetail(item);
            }
        });
    });
}

function warmRepoSummaries(items) {
    items.forEach((item) => {
        fetchRepoDashboard(item.repo_full)
            .then((payload) => {
                hydrateRepoTableRow(item, payload);
            })
            .catch(() => {
                // Keep placeholder cells if this repo summary cannot be enriched.
            });
    });
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
        overviewRepoPreviewState.itemsByRepo = new Map(selectionItems.map((item) => [item.repo_full, item]));

        populateOverviewStats(payload, attentionRepos, highestRiskItems, controlSurfaceRisk, repos);
        setSectionHtml("drift-type-bars", renderDriftTypeBars(controlSurfaceRisk));
        populateCoverageSummary(attentionRepos, repos);
        setSectionHtml("repos-tbody", renderReposTable(repos, attentionRepos));
        renderOverviewQueue(selectionItems, "all");
        bindOverviewFilters(selectionItems);
        bindRepoTablePreview(selectionItems);
        warmRepoSummaries(selectionItems);
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
        setSectionHtml("repo-posture-legend", fallback);
        setSectionHtml("repo-journey-strip", fallback);
        setSectionHtml("detail-attributes", fallback);
        setSectionHtml("detail-evidence-list", `<li>${escapeHtml(message)}</li>`);
        setSectionHtml("drift-type-bars", fallback);
        setSectionHtml("coverage-legend", fallback);
        setSectionHtml("repos-tbody", `<tr><td colspan="7" class="muted">${escapeHtml(message)}</td></tr>`);
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
