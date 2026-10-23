param(
    [switch]$AllowSupabaseTestWrites,
    [switch]$PrepareMetadata,
    [switch]$ApproveTableReaders,
    [switch]$ResumeCurrentRun
)

$ErrorActionPreference = 'Stop'
if (-not $AllowSupabaseTestWrites) {
    throw 'Explicit -AllowSupabaseTestWrites is required for existing Supabase fixture writes.'
}
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    & $python -m scripts.docker_env
    if ($LASTEXITCODE -ne 0) { throw 'Local engine configuration failed.' }
    if ($PrepareMetadata) {
        & $python -m scripts.e2e_environment metadata --allow-supabase-test-writes
        if ($LASTEXITCODE -ne 0) { throw 'Metadata preflight/bootstrap failed.' }
    }
    if (-not (Test-Path '.tmp/docker-integration/hasura.env')) {
        throw 'Reviewed Hasura metadata configuration is required; see docs/DOCKER.md.'
    }
    docker compose --env-file .env.docker -f infra/docker-compose.yml up -d keycloak hasura
    if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed.' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 18; $attempt++) {
        & $python -m scripts.check_docker *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw 'Real Keycloak/Hasura readiness checks failed.' }
    $pointer = '.tmp/docker-integration/current-run.json'
    if (-not $ResumeCurrentRun) {
        if (Test-Path $pointer) {
            $previous = (Get-Content $pointer -Raw | ConvertFrom-Json).directory
            $ledger = Get-Content (Join-Path $previous 'resources.json') -Raw | ConvertFrom-Json
            if (-not $ledger.cleaned) { throw 'An uncleaned run exists. Resume or clean it first.' }
        }
        & $python -m scripts.e2e_environment prepare --allow-supabase-test-writes
        if ($LASTEXITCODE -ne 0) { throw 'Scoped fixture preparation failed; inspect local ledger.' }
    }
    $directory = (Get-Content $pointer -Raw | ConvertFrom-Json).directory
    if ($ApproveTableReaders) {
        & $python -m scripts.e2e_environment approve-table-readers --allow-supabase-test-writes
        if ($LASTEXITCODE -ne 0) { throw 'Explicit table reader approval failed.' }
    }
    docker compose --env-file .env.docker -f infra/docker-compose.yml up -d hasura
    if ($LASTEXITCODE -ne 0) { throw 'Hasura source environment refresh failed.' }
    $env:FLOWBRIDGE_E2E_RUN_DIR = $directory
    try {
        & $python -m pytest tests/e2e/test_existing_supabase.py -m 'integration or e2e' -x -q --tb=short
        $testExit = $LASTEXITCODE
    }
    finally {
        & $python -m scripts.e2e_cleanup $directory --allow-supabase-test-writes
        if ($LASTEXITCODE -ne 0) { throw 'Scoped cleanup incomplete; retain ledger and investigate.' }
        Remove-Item Env:FLOWBRIDGE_E2E_RUN_DIR -ErrorAction SilentlyContinue
    }
    if ($testExit -ne 0) { throw 'Real acceptance failed; see test result and local ledger.' }
}
finally {
    Pop-Location
}
