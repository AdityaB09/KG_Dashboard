param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$envPath = Join-Path $ProjectRoot "backend\.env"
$outPath = Join-Path $ProjectRoot "infra\app\backend_env.production.json"
$secretMapPath = Join-Path $ProjectRoot "infra\app\backend_secret_env.production.json"

if (-not (Test-Path $envPath)) {
  throw "Missing backend/.env: $envPath"
}

$override = @(
  "FRONTEND_APP_URL",
  "FRONTEND_ORIGINS",
  "ORACLE_REDIRECT_URI",
  "ORACLE_LAUNCH_URI",
  "EPIC_REDIRECT_URI",
  "EPIC_LAUNCH_URI",
  "SLM_BASE_URL",
  "SLM_CHAT_PATH",
  "SLM_MODEL",
  "SLM_AUTH_MODE",
  "SLM_AUTH_AUDIENCE",
  "CARDINAL_LLM_PROVIDER",
  "ENVIRONMENT"
)

# For this deployment, keep the production Secret Manager surface explicit.
$productionSecretAllowlist = @(
  "SMTP_USERNAME",
  "SMTP_PASSWORD",
  "MONGODB_URI",
  "ORACLE_MESSAGING_SYSTEM_CLIENT_SECRET"
)

# Sensitive-looking values that are NOT part of this deployment must not fall
# through into plaintext Cloud Run environment variables.
$excludedSensitiveKeys = @(
  "SESSION_SECRET_KEY",
  "ORACLE_MILLENNIUM_BEARER_TOKEN",
  "API_RANGE_API_KEY",
  "SLM_API_KEY"
)

function Test-IsPlaceholderValue([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return $true }

  $v = $Value.Trim()
  $lower = $v.ToLowerInvariant()

  if ($lower -in @(
      "none","null","nil","n/a","na","todo","tbd","changeme","change-me",
      "change_me","placeholder","dummy","example","test","your-secret-here",
      "your_secret_here"
    )) {
    return $true
  }

  if ($lower.StartsWith("your_") -or
      $lower.StartsWith("your-") -or
      ($v.StartsWith("<") -and $v.EndsWith(">"))) {
    return $true
  }

  return $false
}

$all = @{}

foreach ($raw in Get-Content $envPath) {
  $line = $raw.Trim().TrimStart([char]0xFEFF)

  if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
    continue
  }

  $parts = $line.Split("=", 2)
  $key = $parts[0].Trim().TrimStart([char]0xFEFF)
  $value = $parts[1].Trim()

  if ($value.Length -ge 2 -and (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    )) {
    $value = $value.Substring(1, $value.Length - 2)
  }

  if ($key) { $all[$key] = $value }
}

$plainMap = @{}
$secretMap = @{}

foreach ($entry in $all.GetEnumerator()) {
  $key = [string]$entry.Key
  $value = [string]$entry.Value

  if ($override -contains $key -or $key.StartsWith("COLAB_")) {
    continue
  }

  if ($productionSecretAllowlist -contains $key) {
    if (-not (Test-IsPlaceholderValue $value)) {
      # NAMES ONLY. Never write the secret value here.
      $secretMap[$key] = $key
    }
    continue
  }

  if ($excludedSensitiveKeys -contains $key) {
    continue
  }

  # Avoid accidentally placing future obvious credentials in plaintext.
  if ($key -match '(?i)(PASSWORD|CLIENT_SECRET|SECRET_KEY|BEARER_TOKEN|ACCESS_TOKEN|REFRESH_TOKEN|API_KEY)$') {
    Write-Warning "Sensitive-looking key '$key' is not in the production Secret Manager allowlist; excluding it from deployment."
    continue
  }

  $plainMap[$key] = $value
}

$plainMap["ENVIRONMENT"] = "production"
$plainMap["CARDINAL_LLM_PROVIDER"] = "gemma4"
$plainMap["SLM_CHAT_PATH"] = "/v1/chat/completions"
$plainMap["SLM_MODEL"] = "gemma4-26b-a4b-it"
$plainMap["SLM_AUTH_MODE"] = "gcp_identity"
$plainMap["SLM_TIMEOUT_SECONDS"] = "600"
$plainMap["SLM_MAX_OUTPUT_TOKENS"] = "1200"

[System.IO.File]::WriteAllText(
  $outPath,
  ($plainMap | ConvertTo-Json -Depth 6) + [Environment]::NewLine,
  [System.Text.UTF8Encoding]::new($false)
)

[System.IO.File]::WriteAllText(
  $secretMapPath,
  ($secretMap | ConvertTo-Json -Depth 6) + [Environment]::NewLine,
  [System.Text.UTF8Encoding]::new($false)
)

$names = @($secretMap.Keys | Sort-Object)
Write-Host "Refreshed sanitized production config: $outPath" -ForegroundColor Green
Write-Host "Refreshed names-only secret map: $secretMapPath" -ForegroundColor Green
Write-Host ("Detected Secret Manager names ONLY: " + (($names -join ", ") -replace '^$', '<none>')) -ForegroundColor Cyan

if ($secretMap.ContainsKey("SESSION_SECRET_KEY")) {
  throw "SESSION_SECRET_KEY must not be part of this deployment."
}
