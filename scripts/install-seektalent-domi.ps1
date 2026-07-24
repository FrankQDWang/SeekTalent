param(
  [string]$Version = "0.7.49",
  [string]$DomiPython = "",
  [string]$DomiNode = "",
  [string]$WtscliBundleDir = $env:SEEKTALENT_WTSCLI_BUNDLE_DIR,
  [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER
)

function Fail($ReasonCode, $Message) {
  throw "reason_code=$ReasonCode $Message"
}

function Install-SeekTalentDomi {
  param(
    [string]$Version = "0.7.49",
    [string]$DomiPython = "",
    [string]$DomiNode = "",
    [string]$WtscliBundleDir = $env:SEEKTALENT_WTSCLI_BUNDLE_DIR,
    [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER
  )

  $ErrorActionPreference = "Stop"

  if (-not $DomiPython) {
    $DomiPython = Join-Path $env:APPDATA "Domi\runtime\python\bin\python.exe"
  }
  if (-not (Test-Path -Path $DomiPython -PathType Leaf)) {
    Fail "domi_python_missing" "Domi Python was not found: $DomiPython"
  }

  if (-not $DomiNode) {
    $DomiNode = Join-Path $env:APPDATA "Domi\runtime\node\node.exe"
  }
  if (-not (Test-Path -Path $DomiNode -PathType Leaf)) {
    Fail "domi_node_missing" "Domi Node was not found: $DomiNode"
  }
  if (-not $WtscliBundleDir -or -not (Test-Path -Path (Join-Path $WtscliBundleDir "bridge-manifest.json") -PathType Leaf)) {
    Fail "wtscli_bundle_missing" "Set SEEKTALENT_WTSCLI_BUNDLE_DIR to the exact SeekTalent WTSCLI bundle directory."
  }
  if (-not $BrowserBridgeHelper) {
    $BrowserBridgeHelper = Join-Path $PSScriptRoot "install_staging_browser_bridge.py"
  }
  if (-not (Test-Path -Path $BrowserBridgeHelper -PathType Leaf)) {
    Fail "wtscli_bundle_admission_unavailable" "The shared SeekTalent browser bridge admission helper was not found: $BrowserBridgeHelper"
  }
  & $DomiPython $BrowserBridgeHelper --bundle-dir $WtscliBundleDir --verify-only | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fail "wtscli_bundle_invalid" "The exact SeekTalent WTSCLI bundle failed strict admission."
  }

  $Prefix = Join-Path $env:USERPROFILE ".seektalent\python-prefix\$Version"
  $SitePackages = Join-Path $Prefix "Lib\site-packages"
  $BinDir = Join-Path $env:USERPROFILE ".seektalent\bin"
  $CandidateRoot = Join-Path ([IO.Path]::GetTempPath()) ("seektalent-domi-install-" + [Guid]::NewGuid().ToString("N"))
  $CandidatePrefix = Join-Path $CandidateRoot "python-prefix"
  $CandidateSitePackages = Join-Path $CandidatePrefix "Lib\site-packages"
  $PreviousPythonPath = $env:PYTHONPATH
  try {
    New-Item -ItemType Directory -Force -Path $CandidateSitePackages | Out-Null
    & $DomiPython -m pip install --upgrade --ignore-installed --no-cache-dir --target $CandidateSitePackages "seektalent==$Version"
    if ($LASTEXITCODE -ne 0) {
      Fail "seektalent_pypi_install_failed" "Failed to install seektalent==$Version with Domi Python."
    }
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$CandidateSitePackages;$env:PYTHONPATH" } else { $CandidateSitePackages }
    & $DomiPython -m seektalent.domi_bootstrap `
      --package-version $Version `
      --python-path $SitePackages `
      --python-prefix-candidate $CandidatePrefix `
      --python-prefix-target $Prefix `
      --domi-python $DomiPython `
      --domi-node $DomiNode `
      --browser-bridge-bundle-dir $WtscliBundleDir `
      --bin-dir $BinDir `
      --print-json
    if ($LASTEXITCODE -ne 0) {
      Fail "seektalent_domi_bootstrap_failed" "Failed to prepare the seektalent command shim."
    }
  } finally {
    if ($null -eq $PreviousPythonPath) {
      Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
      $env:PYTHONPATH = $PreviousPythonPath
    }
    Remove-Item -Recurse -Force -Path $CandidateRoot -ErrorAction SilentlyContinue
  }

  if (($env:Path -split ";") -notcontains $BinDir) {
    $env:Path = "$BinDir;$env:Path"
  }

  Write-Host "SeekTalent Domi install ready. Run: seektalent workbench"
}

if ($MyInvocation.MyCommand.Path -and $MyInvocation.InvocationName -ne ".") {
  try {
    Install-SeekTalentDomi -Version $Version -DomiPython $DomiPython -DomiNode $DomiNode -WtscliBundleDir $WtscliBundleDir -BrowserBridgeHelper $BrowserBridgeHelper
  } catch {
    Write-Error $_
    exit 1
  }
}
