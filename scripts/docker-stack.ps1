param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "restart", "logs")]
    [string]$Action = "up",

    [Parameter(Position = 1)]
    [ValidateSet("sqlite", "postgres")]
    [string]$Mode = "sqlite",

    [switch]$FullStack
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    $composeArgs = @()
    $serviceArgs = @("api")

    if ($FullStack) {
        $serviceArgs = @("api", "webhook-ingress", "worker")
    }

    # Keep the default local modes focused on UI/API inspection.
    # If the caller wants webhook + worker they must opt in and provide real creds.
    if (-not $FullStack) {
        $env:GITHUB_APP_ID = ""
        $env:GITHUB_PRIVATE_KEY_PATH = ""
        $env:GITHUB_APP_PRIVATE_KEY = ""
    }

    if ($Mode -eq "sqlite") {
        $env:APP_ENV = "local"
        $env:APP_BASE_URL = "http://127.0.0.1:8011"
        $env:API_ADMIN_TOKEN = "local-admin-token"
        $env:APP_ENCRYPTION_KEY = "local-dev-encryption-key"
        if (-not $env:SESSION_COOKIE_SECURE) {
            $env:SESSION_COOKIE_SECURE = "false"
        }
        $composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.sqlite.yml")
    }
    else {
        if ($FullStack) {
            $composeArgs = @("--env-file", "docker-compose.local.env.example")
        }
        else {
            $env:APP_ENV = "local"
            $env:APP_BASE_URL = "http://127.0.0.1:8011"
            $env:API_ADMIN_TOKEN = "local-admin-token"
            $env:APP_ENCRYPTION_KEY = "local-dev-encryption-key"
            if (-not $env:SESSION_COOKIE_SECURE) {
                $env:SESSION_COOKIE_SECURE = "false"
            }
            $composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.postgres.yml")
        }
    }

    switch ($Action) {
        "up" {
            & docker compose @composeArgs up -d --build @serviceArgs
        }
        "down" {
            & docker compose @composeArgs down
        }
        "restart" {
            & docker compose @composeArgs down
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
            & docker compose @composeArgs up -d --build @serviceArgs
        }
        "logs" {
            & docker compose @composeArgs logs -f
        }
    }

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}