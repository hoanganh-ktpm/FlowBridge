param(
    [string]$PostgresBin = "$env:ProgramFiles\PostgreSQL\17\bin",
    [string]$OpenSsl = "$env:ProgramFiles\Git\usr\bin\openssl.exe",
    [int]$Port = 55439,
    [string]$TestPath = "tests/core/persistence"
)

$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$cluster = [IO.Path]::GetFullPath((Join-Path $workspace ('.tmp/pg-onboarding-' + [guid]::NewGuid().ToString('N'))))
if (-not $cluster.StartsWith((Join-Path $workspace '.tmp') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Test cluster must stay inside the workspace temporary directory'
}
foreach ($binary in @('initdb.exe', 'pg_ctl.exe', 'postgres.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PostgresBin $binary))) { throw "Missing PostgreSQL binary: $binary" }
}
if (-not (Test-Path -LiteralPath $OpenSsl)) { throw 'OpenSSL is required for this isolated TLS test cluster' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Choose an unprivileged local test port' }
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
try { $listener.Start() } finally { $listener.Stop() }
$python = Join-Path $workspace '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the locked Python dependencies first' }
$previousAdmin = $env:DXOS_TEST_ADMIN_URL
$started = $false
$exitCode = 1
Push-Location $workspace
try {
    & (Join-Path $PostgresBin 'initdb.exe') -D $cluster -U flowbridge_test --auth=trust --encoding=UTF8 --no-locale *> ($cluster + '.init.log')
    if ($LASTEXITCODE -ne 0) { throw "initdb failed; inspect $cluster.init.log" }
    & $OpenSsl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=localhost' -keyout (Join-Path $cluster 'server.key') -out (Join-Path $cluster 'server.crt') *> (Join-Path $cluster 'tls.log')
    if ($LASTEXITCODE -ne 0) { throw 'Local test certificate generation failed' }
    & (Join-Path $PostgresBin 'pg_ctl.exe') -D $cluster -l (Join-Path $cluster 'postgres.log') -o "-h 127.0.0.1 -p $Port -c ssl=on" -w start
    if ($LASTEXITCODE -ne 0) { throw 'Private test PostgreSQL failed to start' }
    $started = $true
    $env:DXOS_TEST_ADMIN_URL = "postgresql+psycopg://flowbridge_test:SYNTHETIC_LOCAL_ONLY@127.0.0.1:$Port/postgres?sslmode=require"
    & $python -m pytest $TestPath -m integration -q --tb=short
    $exitCode = $LASTEXITCODE
} finally {
    if ($started) {
        & (Join-Path $PostgresBin 'pg_ctl.exe') -D $cluster -m fast -w stop
        if ($LASTEXITCODE -ne 0) { Write-Warning "Test server needs manual shutdown: $cluster" }
    }
    if ($null -eq $previousAdmin) { Remove-Item Env:DXOS_TEST_ADMIN_URL -ErrorAction SilentlyContinue }
    else { $env:DXOS_TEST_ADMIN_URL = $previousAdmin }
    Pop-Location
}
exit $exitCode
