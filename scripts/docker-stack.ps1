# DEV-ONLY: This script is intended for local development and rehearsal.
# Do not use it as a production deployment method.

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

function Ensure-LocalAppEncryptionKey {
    if ($env:APP_ENCRYPTION_KEY) {
        return
    }

    $randomBytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($randomBytes)
    }
    finally {
        $rng.Dispose()
    }
    $env:APP_ENCRYPTION_KEY = [Convert]::ToBase64String($randomBytes)
}

try {
    $composeArgs = @()
    $serviceArgs = @("api")
    $upArgs = @("up", "-d", "--build")

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
        Ensure-LocalAppEncryptionKey
        if (-not $env:SESSION_COOKIE_SECURE) {
            $env:SESSION_COOKIE_SECURE = "false"
        }
        $composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.sqlite.yml")
        if (-not $FullStack) {
            $upArgs += "--no-deps"
        }
    }
    else {
        if ($FullStack) {
            $composeArgs = @("--env-file", "docker-compose.local.env.example")
        }
        else {
            $env:APP_ENV = "local"
            $env:APP_BASE_URL = "http://127.0.0.1:8011"
            $env:API_ADMIN_TOKEN = "local-admin-token"
            Ensure-LocalAppEncryptionKey
            if (-not $env:SESSION_COOKIE_SECURE) {
                $env:SESSION_COOKIE_SECURE = "false"
            }
            $composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.postgres.yml")
        }
    }

    switch ($Action) {
        "up" {
            & docker compose @composeArgs @upArgs @serviceArgs
        }
        "down" {
            & docker compose @composeArgs down
        }
        "restart" {
            & docker compose @composeArgs down
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
            & docker compose @composeArgs @upArgs @serviceArgs
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