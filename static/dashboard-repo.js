const repoFull = document.querySelector('meta[name="promptdrift-repo-full"]')?.getAttribute("content") || "";

function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
}

function renderLeaderboard(items) {
    if (!items.length) {
        return '<div class="muted">No pull-request drift samples have been recorded yet.</div>';
    }
    return `<table><thead><tr><th>Artifact</th><th>Type</th><th>Drift magnitude</th><th>Capability shift</th><th>Autonomy shift</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td>${item.artifact_path}</td>
                    <td>${item.artifact_type}</td>
                    <td>${item.drift_magnitude.toFixed(3)}</td>
                    <td>${item.capability_shift.toFixed(3)}</td>
                    <td>${item.autonomy_shift.toFixed(3)}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

function renderArtifacts(items) {
    if (!items.length) {
        return '<div class="muted">No onboarded artifacts were found for this repository yet.</div>';
    }
    return `<table><thead><tr><th>Artifact</th><th>Baseline lines</th><th>Historical versions</th><th>Historical drift</th><th>PR profiles</th><th>Latest PR semantic distance</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td><strong>${item.artifact_path}</strong><br><span class="muted">${item.artifact_type}</span></td>
                    <td>${item.baseline_line_count}</td>
                    <td>${item.historical_version_count}</td>
                    <td>${item.latest_historical_drift_magnitude.toFixed(3)}</td>
                    <td>${item.pr_profile_count}</td>
                    <td>${item.latest_pr_semantic_distance.toFixed(3)}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

function renderInsights(items) {
    if (!items.length) {
        return '<div class="muted">No prioritized insights yet. Once drift history grows, this panel will highlight what needs review first.</div>';
    }
    return `<div class="stack">${items
        .map(
            (item) => `
                <div>
                    <div class="priority priority-${item.priority}">${item.priority.replace("_", " ")}</div>
                    <div class="insight-title">${item.title}</div>
                    <div><strong>${item.artifact_path}</strong> <span class="muted">(${item.artifact_type})</span></div>
                    <div class="muted" style="margin-top:6px;">${item.rationale}</div>
                    <div style="margin-top:6px;">${item.recommended_action}</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaces(items) {
    if (!items.length) {
        return '<div class="muted">No grouped control surfaces yet.</div>';
    }
    return `<table><thead><tr><th>Group</th><th>Artifacts</th><th>High confidence</th><th>Top examples</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td>${item.label}</td>
                    <td>${item.artifact_count}</td>
                    <td>${item.high_confidence_count}</td>
                    <td>${item.top_artifact_paths.join("<br>")}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

async function loadDashboard() {
    const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`);
    const payload = await response.json();

    document.getElementById("summary").innerHTML = [
        metricCard(
            "Onboarded artifacts",
            payload.onboarding ? payload.onboarding.discovered_artifact_count : 0,
            payload.onboarding ? `Default branch: ${payload.onboarding.default_branch}` : "No onboarding yet"
        ),
        metricCard("Baseline versions", payload.baseline_version_count, `Repo: ${payload.repo_full}`),
        metricCard(
            "Backfill jobs",
            payload.backfill.job_count,
            `Completed: ${payload.backfill.completed_job_count} · Failed: ${payload.backfill.failed_job_count}`
        ),
        metricCard(
            "Historical versions",
            payload.backfill.total_historical_versions,
            `Historical profiles: ${payload.backfill.total_historical_profiles}`
        ),
        metricCard("PR audits", payload.pull_request_audit_count, `PR profiles: ${payload.drift_summary.profile_count}`),
        metricCard(
            "Avg semantic distance",
            payload.drift_summary.avg_semantic_distance.toFixed(3),
            `Highest capability artifact: ${payload.drift_summary.highest_capability_artifact_path || "n/a"}`
        ),
    ].join("");

    document.getElementById("insights").innerHTML = renderInsights(payload.insights);
    document.getElementById("control-surfaces").innerHTML = renderControlSurfaces(payload.control_surface_groups);
    document.getElementById("leaderboard").innerHTML = renderLeaderboard(payload.top_drifting_artifacts);
    document.getElementById("artifacts").innerHTML = renderArtifacts(payload.artifacts);
}

loadDashboard();
