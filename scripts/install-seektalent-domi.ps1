param(
  [string]$Version = "0.8.3",
  [string]$DomiPython = "",
  [string]$DomiNode = "",
  [string]$WtscliBundleDir = $env:SEEKTALENT_WTSCLI_BUNDLE_DIR,
  [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER,
  [string]$PreparedWtscliRuntime = $env:SEEKTALENT_WTSCLI_PREPARED_RUNTIME,
  [string]$DeliveryManifest = $env:SEEKTALENT_DELIVERY_MANIFEST,
  [string]$InstallHome = $env:SEEKTALENT_INSTALL_HOME
)

function Fail($ReasonCode, $Message) {
  throw "reason_code=$ReasonCode $Message"
}

function Install-SeekTalentDomi {
  param(
    [string]$Version = "0.8.3",
    [string]$DomiPython = "",
    [string]$DomiNode = "",
    [string]$WtscliBundleDir = $env:SEEKTALENT_WTSCLI_BUNDLE_DIR,
    [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER,
    [string]$PreparedWtscliRuntime = $env:SEEKTALENT_WTSCLI_PREPARED_RUNTIME,
    [string]$DeliveryManifest = $env:SEEKTALENT_DELIVERY_MANIFEST,
    [string]$InstallHome = $env:SEEKTALENT_INSTALL_HOME
  )

  $ErrorActionPreference = "Stop"
  if (-not $InstallHome) {
    $InstallHome = $env:USERPROFILE
  }

  if (-not $DomiPython) {
    $DomiPython = $env:SEEKTALENT_DOMI_PYTHON
  }
 if (-not (Test-Path -Path $DomiPython -PathType Leaf)) {
    Fail "domi_python_missing" "Set DOMI_PYTHON or SEEKTALENT_DOMI_PYTHON to the Domi-provided Python executable: $DomiPython"
 }

  if (-not $DomiNode) {
    $DomiNode = if ($env:SEEKTALENT_DOMI_NODE) { $env:SEEKTALENT_DOMI_NODE } else { $env:DOMI_NODE }
  }
 if (-not (Test-Path -Path $DomiNode -PathType Leaf)) {
    Fail "domi_node_missing" "Set DOMI_NODE or SEEKTALENT_DOMI_NODE to the Domi-provided Node executable: $DomiNode"
 }
  if (-not $WtscliBundleDir) {
    $WtscliBundleDir = Join-Path $PSScriptRoot "wtscli-browser-bridge"
  }
  if (-not (Test-Path -Path (Join-Path $WtscliBundleDir "bridge-manifest.json") -PathType Leaf)) {
    Fail "wtscli_bundle_missing" "The exact WTSCLI bundle was not found in the SeekTalent product package: $WtscliBundleDir"
  }
  if (-not $BrowserBridgeHelper) {
    $BrowserBridgeHelper = Join-Path $PSScriptRoot "install_staging_browser_bridge.py"
  }
  if (-not (Test-Path -Path $BrowserBridgeHelper -PathType Leaf)) {
    Fail "wtscli_bundle_admission_unavailable" "The shared SeekTalent browser bridge admission helper was not found: $BrowserBridgeHelper"
  }
  if (-not $PreparedWtscliRuntime) {
    $PreparedWtscliRuntime = Join-Path $PSScriptRoot "wtscli-runtime.zip"
  }
  $ProductWheels = @(Get-ChildItem -Path $PSScriptRoot -Filter "seektalent-*.whl" -File)
  $ProductWheel = if ($ProductWheels.Count -eq 1) { $ProductWheels[0].FullName } else { "" }
  $AdmissionPythonPath = $env:PYTHONPATH
  try {
    if ($ProductWheel) {
      $env:PYTHONPATH = if ($env:PYTHONPATH) { "$ProductWheel;$env:PYTHONPATH" } else { $ProductWheel }
    }
    & $DomiPython $BrowserBridgeHelper --bundle-dir $WtscliBundleDir --verify-only | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Fail "wtscli_bundle_invalid" "The exact SeekTalent WTSCLI bundle failed strict admission."
    }
  } finally {
    if ($null -eq $AdmissionPythonPath) {
      Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
      $env:PYTHONPATH = $AdmissionPythonPath
    }
  }
  if (-not (Test-Path -Path $PreparedWtscliRuntime -PathType Leaf)) {
    Fail "wtscli_runtime_missing" "The prepared WTSCLI runtime was not found in the SeekTalent product package: $PreparedWtscliRuntime"
  }
  if (-not $DeliveryManifest) {
    $DeliveryManifest = Join-Path $PSScriptRoot "delivery-manifest.json"
  }
  $RuntimeVerifier = Join-Path $PSScriptRoot "verify_domi_host_runtime.py"
  $Wheelhouse = Join-Path $PSScriptRoot "python-wheelhouse"
  if (-not $ProductWheel -or
      -not (Test-Path -Path $DeliveryManifest -PathType Leaf) -or
      -not (Test-Path -Path $RuntimeVerifier -PathType Leaf) -or
      -not (Test-Path -Path $Wheelhouse -PathType Container)) {
    Fail "delivery_manifest_missing" "The exact manifest, verifier, wheel, and offline wheelhouse are required."
  }
  & $DomiPython $RuntimeVerifier validate-delivery --node $DomiNode --manifest $DeliveryManifest
  if ($LASTEXITCODE -ne 0) {
    Fail "delivery_preflight_failed" "The delivery payload or Domi runtime failed exact validation."
  }
  $DeliveryManifestArgs = @("--delivery-manifest", $DeliveryManifest)

  $Prefix = Join-Path $InstallHome ".seektalent\python-prefix\$Version"
  $SitePackages = Join-Path $Prefix "Lib\site-packages"
  $BinDir = Join-Path $InstallHome ".seektalent\bin"
  $CandidateRoot = Join-Path ([IO.Path]::GetTempPath()) ("seektalent-domi-install-" + [Guid]::NewGuid().ToString("N"))
  $CandidatePrefix = Join-Path $CandidateRoot "python-prefix"
  $CandidateSitePackages = Join-Path $CandidatePrefix "Lib\site-packages"
  $PreparedRuntimeDir = Join-Path $CandidateRoot "wtscli-runtime"
  $PreviousPythonPath = $env:PYTHONPATH
  try {
    New-Item -ItemType Directory -Force -Path $CandidateSitePackages | Out-Null
    New-Item -ItemType Directory -Force -Path $PreparedRuntimeDir | Out-Null
    & $DomiPython -m pip install --no-index --find-links $Wheelhouse --upgrade --ignore-installed --no-cache-dir --target $CandidateSitePackages $ProductWheel
    if ($LASTEXITCODE -ne 0) {
      Fail "seektalent_offline_install_failed" "Failed to install the exact SeekTalent wheel from its offline wheelhouse."
    }
    Expand-Archive -Path $PreparedWtscliRuntime -DestinationPath $PreparedRuntimeDir -Force
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$CandidateSitePackages;$env:PYTHONPATH" } else { $CandidateSitePackages }
    & $DomiPython -m seektalent.domi_bootstrap `
      --package-version $Version `
      --home $InstallHome `
      --python-path $SitePackages `
      --python-prefix-candidate $CandidatePrefix `
      --python-prefix-target $Prefix `
      --domi-python $DomiPython `
      --domi-node $DomiNode `
      --browser-bridge-bundle-dir $WtscliBundleDir `
      --browser-bridge-prepared-runtime-dir $PreparedRuntimeDir `
      --product-wheel $ProductWheel `
      @DeliveryManifestArgs `
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

  Write-Host "SeekTalent Domi install ready. Start with: $PSScriptRoot\start-seektalent-domi.ps1"
  Write-Host "Chrome 扩展目录：$InstallHome\.seektalent\chrome-extension\wtscli"
  Write-Host "打开 chrome://extensions，启用“开发者模式”，选择“加载已解压的扩展程序”，并选择上面的唯一目录。"
  Write-Host "升级后请在该页面点击 WTSCLI 的“重新加载”；若仍显示旧版本，请完全退出并重启 Chrome。"
  Write-Host "检查：seektalent browser-check"
}

if ($MyInvocation.MyCommand.Path -and $MyInvocation.InvocationName -ne ".") {
  try {
    Install-SeekTalentDomi -Version $Version -DomiPython $DomiPython -DomiNode $DomiNode -WtscliBundleDir $WtscliBundleDir -BrowserBridgeHelper $BrowserBridgeHelper -PreparedWtscliRuntime $PreparedWtscliRuntime -DeliveryManifest $DeliveryManifest -InstallHome $InstallHome
  } catch {
    Write-Error $_
    exit 1
  }
}
