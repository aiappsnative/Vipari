function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
}

function renderAttentionRepos(items) {
    if (!items.length) {
        return '<div class="muted">No repo priorities yet. Onboard a repository to populate the decision queue.</div>';
    }
    return `<div class="stack">${items
        .map(
            (repo) => `
                <a class="attention-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
                    <div class="priority priority-${repo.highest_priority}">${repo.highest_priority.replace("_", " ")}</div>
                    <div class="insight-title">${repo.repo_full}</div>
                    <div>${repo.highest_insight_title || "No prioritized repo insight yet"}</div>
                    <div class="muted meta-tight">${repo.highest_insight_artifact_path || "No lead artifact yet"}</div>
                    <div class="meta-tight muted">Review now: ${repo.review_now_count} · Watch: ${repo.watch_count} · Baseline review: ${repo.baseline_review_count}</div>
                </a>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaceCoverage(items) {
    if (!items.length) {
        return '<div class="muted">No control surface coverage yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count), 1);
    return `<div class="stack">${items
        .map(
            (item) => `
                <div>
                    <div class="coverage-row">
                        <strong>${item.label}</strong>
                        <span class="muted">${item.repo_count} repos · ${item.artifact_count} artifacts</span>
                    </div>
                    <div class="bar-track"><div class="bar-fill" style="width: ${(item.artifact_count / maxArtifacts) * 100}%"></div></div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderRepoTable(items) {
    if (!items.length) {
        return '<div class="muted">No onboarded repositories yet. Use the onboarding API first.</div>';
    }
    return `<table><thead><tr><th>Repository</th><th>Default branch</th><th>Artifacts</th><th>Status</th></tr></thead><tbody>${items
        .map(
            (repo) => `
                <tr>
                    <td><a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${repo.repo_full}</a></td>
                    <td>${repo.default_branch}</td>
                    <td>${repo.discovered_artifact_count}</td>
                    <td><span class="pill">${repo.onboarding_status}</span></td>
                </tr>
            `
        )
        .join("")}</tbody></table>`;
}

async function loadOverview() {
    const response = await fetch("/api/dashboard/overview");
    const payload = await response.json();

    document.getElementById("overview-metrics").innerHTML = payload.metrics
        .map((metric) => metricCard(metric.label, metric.value, metric.detail))
        .join("");
    document.getElementById("attention-repos").innerHTML = renderAttentionRepos(payload.attention_repos);
    document.getElementById("control-surface-coverage").innerHTML = renderControlSurfaceCoverage(payload.control_surface_coverage);
    document.getElementById("repos").innerHTML = renderRepoTable(payload.repos);
}

loadOverview();
