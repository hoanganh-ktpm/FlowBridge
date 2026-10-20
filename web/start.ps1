param([switch]$WithML, [switch]$WithRAG)
$ErrorActionPreference = 'Stop'
$flowbridgeRoot = Split-Path -Parent $PSScriptRoot
$previousHubSetting = $env:FLOWBRIDGE_AGENT_HUB_ENABLED
Push-Location -LiteralPath $flowbridgeRoot
try {
    $env:FLOWBRIDGE_AGENT_HUB_ENABLED = 'true'
    $flowbridgeArgs = @('up')
    if ($WithML) { $flowbridgeArgs += '--ml' }
    if ($WithRAG) { $flowbridgeArgs += '--rag' }
    & (Join-Path $flowbridgeRoot 'chat-docker.cmd') @flowbridgeArgs
    if ($LASTEXITCODE -ne 0) { throw "FlowBridge startup failed ($LASTEXITCODE)." }
} finally {
    $env:FLOWBRIDGE_AGENT_HUB_ENABLED = $previousHubSetting
    Pop-Location
}
