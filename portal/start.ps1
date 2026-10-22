param()
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    & npm.cmd run dev
    if ($LASTEXITCODE -ne 0) { throw "Portal dev server failed ($LASTEXITCODE)." }
} finally {
    Pop-Location
}
