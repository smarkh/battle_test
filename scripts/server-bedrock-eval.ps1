<#
Start an evaluation on the smark_iq server with the models on Amazon Bedrock
and semantic search on the server. Run it on the laptop, from the repo:

    aws sso login --profile battle-test-eval
    .\scripts\server-bedrock-eval.ps1 -Label bedrock-gptoss120b-all-semantic

It copies config.server-bedrock.toml and short-lived AWS credentials from the
laptop's sign-in to C:\Users\smark\battle-eval on the server, then starts the
run in the background there as the container `battle-eval`. The laptop can
sleep once it has started. See docs/running-on-bedrock.md for following it,
copying the results back and cleaning up.

FICTIONAL CASES ONLY. Every run costs money: get an estimate first.
#>
param(
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$Model = "gpt-oss-120b",
    [string]$Checker = "gpt-oss-120b",
    [int]$Repeats = 3,
    [string[]]$Case = @(),
    # The Bedrock-only sign-in (docs/running-on-bedrock.md). Not
    # AdministratorAccess: these credentials are copied to the server.
    [string]$Profile = "battle-test-eval",
    # The credentials must last the whole run. 21 runs on gpt-oss took
    # 2 h 14 min on 2026-11-05.
    [double]$MinHours = 3,
    # Check everything and show the server command, but copy and start nothing.
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Server = "smark@smark-iq"
$ServerRepo = "C:\Users\smark\Documents\work\battle_test"
$ServerDir = "C:\Users\smark\battle-eval"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $RepoRoot "config.server-bedrock.toml"

if (-not (Test-Path $Config)) { throw "Not found: $Config" }

# 1. Short-lived credentials from the laptop's SSO sign-in.
$json = aws configure export-credentials --profile $Profile --format process
if ($LASTEXITCODE -ne 0 -or -not $json) {
    throw "No AWS credentials. Run: aws sso login --profile $Profile"
}
$creds = ($json -join "`n") | ConvertFrom-Json
$hoursLeft = ([datetime]$creds.Expiration).ToUniversalTime().Subtract((Get-Date).ToUniversalTime()).TotalHours
Write-Host ("Credentials last {0:N1} more hours." -f $hoursLeft)
if ($hoursLeft -lt $MinHours) {
    throw ("They'd run out before the run ends (need {0} h). Sign in again with " +
           "'aws sso login --profile {1}'. If they're still short, the permission set's " +
           "session duration is too low: see docs/running-on-bedrock.md.") -f $MinHours, $Profile
}

# 2. Nothing else may be using the container name.
$existing = ssh $Server "docker ps -a --filter name=^battle-eval$ --format '{{.Status}}'"
if ($existing) {
    throw "A battle-eval container already exists on the server ($existing). Copy its results and remove it first: docker rm battle-eval"
}

# 3. The server command. No secrets in it: the container reads them from the
# mounted folder.
$evalArgs = @("--config", "/eval/config.server-bedrock.toml", "--model", $Model,
              "--checker", $Checker, "--repeats", $Repeats, "--label", $Label)
foreach ($c in $Case) { $evalArgs += @("--case", $c) }
$remote = "cd $ServerRepo; docker compose run -d --no-deps --name battle-eval " +
          "-v ${ServerDir}:/eval:ro -e AWS_SHARED_CREDENTIALS_FILE=/eval/credentials " +
          "battle-test python -m battle_test.evaluate " + ($evalArgs -join " ")

if ($DryRun) {
    Write-Host "Dry run. Would copy the config and credentials to ${Server}:$ServerDir and run:"
    Write-Host "  $remote"
    return
}

# 4. Copy the config and credentials. The credentials file is written to a
# temporary folder only for the copy, then deleted.
$tmp = Join-Path $env:TEMP ("battle-eval-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Copy-Item $Config (Join-Path $tmp "config.server-bedrock.toml")
    $lines = @(
        "[default]",
        "aws_access_key_id = $($creds.AccessKeyId)",
        "aws_secret_access_key = $($creds.SecretAccessKey)",
        "aws_session_token = $($creds.SessionToken)"
    )
    # ASCII, no BOM: botocore can't read a file that starts with one.
    [System.IO.File]::WriteAllLines((Join-Path $tmp "credentials"), $lines, (New-Object System.Text.ASCIIEncoding))
    ssh $Server "New-Item -ItemType Directory -Force $ServerDir | Out-Null"
    scp -q (Join-Path $tmp "config.server-bedrock.toml") (Join-Path $tmp "credentials") "${Server}:battle-eval/"
    if ($LASTEXITCODE -ne 0) { throw "Copying to the server failed." }
}
finally {
    Remove-Item -Recurse -Force $tmp
}

# 5. Start it.
ssh $Server $remote
if ($LASTEXITCODE -ne 0) { throw "The run didn't start. See the output above." }
Write-Host "Started. Follow it with: ssh $Server `"docker logs --tail 5 battle-eval`""
