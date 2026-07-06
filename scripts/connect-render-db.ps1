param(
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,
    [string]$ServiceName = "sozeracke-blog",
    [string]$DatabaseName = "blog-db"
)

$ErrorActionPreference = "Stop"
$base = "https://api.render.com/v1"
$headers = @{
    Authorization = "Bearer $ApiKey"
    Accept        = "application/json"
    "Content-Type" = "application/json"
}

function Get-RenderJson($Uri) {
    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get
}

function Put-RenderJson($Uri, $Body) {
    $json = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Put -Body $json
}

function Post-RenderJson($Uri, $Body = "{}") {
    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Post -Body $Body
}

Write-Host "Looking for service '$ServiceName'..."
$services = Get-RenderJson "$base/services?name=$ServiceName&limit=20"
$serviceItem = $services | Where-Object { $_.service.name -eq $ServiceName } | Select-Object -First 1
if (-not $serviceItem) {
    throw "Service '$ServiceName' not found"
}
$serviceId = $serviceItem.service.id
Write-Host "Service ID: $serviceId"

Write-Host "Looking for database '$DatabaseName'..."
$postgresList = Get-RenderJson "$base/postgres?name=$DatabaseName&limit=20"
$dbItem = $postgresList | Where-Object { $_.postgres.name -eq $DatabaseName } | Select-Object -First 1
if (-not $dbItem) {
    throw "Database '$DatabaseName' not found"
}
$postgresId = $dbItem.postgres.id
Write-Host "Database ID: $postgresId"

Write-Host "Fetching internal connection string..."
$conn = Get-RenderJson "$base/postgres/$postgresId/connection-info"
$dbUrl = $conn.internalConnectionString
if (-not $dbUrl) {
    throw "internalConnectionString is empty"
}

Write-Host "Setting DATABASE_URL on service..."
Put-RenderJson "$base/services/$serviceId/env-vars/DATABASE_URL" @{ value = $dbUrl } | Out-Null

Write-Host "Triggering deploy..."
Post-RenderJson "$base/services/$serviceId/deploys" '{"clearCache":"do_not_clear"}' | Out-Null

Write-Host "Done. Wait 2-3 minutes, then open:"
Write-Host "https://sozeracke-blog.onrender.com/health"