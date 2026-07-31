param(
  [string]$InstallHome = $env:SEEKTALENT_INSTALL_HOME
)

$ErrorActionPreference = "Stop"

function Fail($ReasonCode, $Message) {
  throw "reason_code=$ReasonCode $Message"
}

if (-not $InstallHome) {
  $InstallHome = $env:USERPROFILE
}
if (-not $InstallHome) {
  Fail "seektalent_install_home_missing" "SEEKTALENT_INSTALL_HOME or USERPROFILE is required."
}
if (-not $env:SEEKTALENT_DOMI_JWT) {
  Fail "seektalent_domi_jwt_missing" "The Domi host must inject SEEKTALENT_DOMI_JWT before starting SeekTalent."
}

$DomiPython = if ($env:SEEKTALENT_DOMI_PYTHON) { $env:SEEKTALENT_DOMI_PYTHON } elseif ($env:DOMI_PYTHON) { $env:DOMI_PYTHON } else { "" }
if (-not $DomiPython -or -not (Test-Path -Path $DomiPython -PathType Leaf)) {
  Fail "domi_python_missing" "The Domi host must provide an executable DOMI_PYTHON."
}

$DomiNode = if ($env:SEEKTALENT_DOMI_NODE) { $env:SEEKTALENT_DOMI_NODE } elseif ($env:DOMI_NODE) { $env:DOMI_NODE } else { "" }
if ($DomiNode -and (Test-Path -Path $DomiNode -PathType Container)) {
  $DomiNode = Join-Path $DomiNode "node.exe"
}
if (-not $DomiNode -or -not (Test-Path -Path $DomiNode -PathType Leaf)) {
  Fail "domi_node_missing" "The Domi host must provide an executable DOMI_NODE."
}

$SeekTalentRoot = Join-Path $InstallHome ".seektalent"
$Receipt = Join-Path $SeekTalentRoot "install-receipt.json"
$BridgeManifest = Join-Path $SeekTalentRoot "browser-bridge\bridge-manifest.json"
$RuntimeRoot = Join-Path $SeekTalentRoot "wtscli-runtime"
$ExtensionRoot = Join-Path $SeekTalentRoot "chrome-extension\wtscli"

if (-not (Test-Path -Path $Receipt -PathType Leaf)) { Fail "seektalent_receipt_missing" "The exact SeekTalent install receipt is missing." }
$ProductVersion = & $DomiPython -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['productVersion'])" $Receipt
if ($LASTEXITCODE -ne 0 -or -not $ProductVersion) { Fail "seektalent_receipt_invalid" "The installed SeekTalent receipt cannot be read." }
$ReleasePrefix = Join-Path $SeekTalentRoot "python-prefix\$ProductVersion"
$ReleaseSitePackages = Join-Path $ReleasePrefix "Lib\site-packages"
if (-not (Test-Path -Path $ReleaseSitePackages -PathType Container)) { $ReleaseSitePackages = Join-Path $ReleasePrefix "site-packages" }
if (-not (Test-Path -Path $ReleaseSitePackages -PathType Container)) { Fail "seektalent_release_prefix_missing" "The exact installed SeekTalent Python prefix is missing." }

$PreviousLocation = Get-Location
try {
  Set-Location ([IO.Path]::GetTempPath())
  $env:PYTHONPATH = if ($env:PYTHONPATH) { "$ReleaseSitePackages;$env:PYTHONPATH" } else { $ReleaseSitePackages }
  $env:PYTHONNOUSERSITE = "1"
  & $DomiPython -m seektalent.installed_domi_release --home $InstallHome | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "seektalent_exact_release_invalid" "The installed SeekTalent package and WTSCLI pair failed exact validation." }
} finally {
  Set-Location $PreviousLocation
}

if (-not (Test-Path -Path $BridgeManifest -PathType Leaf)) { Fail "wtscli_bundle_missing" "The installed WTSCLI bridge manifest is missing." }
if (-not (Test-Path -Path $RuntimeRoot -PathType Container)) { Fail "wtscli_runtime_missing" "The installed WTSCLI runtime is missing." }
if (-not (Test-Path -Path $ExtensionRoot -PathType Container)) { Fail "wtscli_extension_missing" "The installed WTSCLI extension tree is missing." }
$SeekTalentBin = Join-Path $SeekTalentRoot "bin\seektalent.cmd"
if (-not (Test-Path -Path $SeekTalentBin -PathType Leaf)) { Fail "seektalent_bin_missing" "The installed SeekTalent command is missing." }

$env:SEEKTALENT_DOMI_PYTHON = $DomiPython
$env:SEEKTALENT_DOMI_NODE = $DomiNode
$env:DOMI_NODE = $DomiNode
$env:SEEKTALENT_TEXT_LLM_PROVIDER_LABEL = "domi"
$env:SEEKTALENT_RUNTIME_MODE = "prod"
$env:SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE = "prod"
$env:SEEKTALENT_WORKSPACE_ROOT = $InstallHome
$env:SEEKTALENT_PROVIDER_NAME = "liepin"
$env:SEEKTALENT_LIEPIN_WORKER_MODE = "opencli"
$env:SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND = "opencli"
if (-not $env:SEEKTALENT_DOMI_LLM_CHANNEL) { $env:SEEKTALENT_DOMI_LLM_CHANNEL = "seek_talent" }

& $SeekTalentBin workbench @args
exit $LASTEXITCODE
