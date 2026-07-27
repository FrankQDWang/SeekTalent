param(
  [string]$Version = "0.7.49",
  [string]$DomiPython = "",
  [string]$DomiNode = "",
  [string]$WtscliBundleDir = $env:SEEKTALENT_WTSCLI_BUNDLE_DIR,
  [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER,
  [string]$PreparedWtscliRuntime = $env:SEEKTALENT_WTSCLI_PREPARED_RUNTIME
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
    [string]$BrowserBridgeHelper = $env:SEEKTALENT_BROWSER_BRIDGE_HELPER,
    [string]$PreparedWtscliRuntime = $env:SEEKTALENT_WTSCLI_PREPARED_RUNTIME
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

  $Prefix = Join-Path $env:USERPROFILE ".seektalent\python-prefix\$Version"
  $SitePackages = Join-Path $Prefix "Lib\site-packages"
  $BinDir = Join-Path $env:USERPROFILE ".seektalent\bin"
  $CandidateRoot = Join-Path ([IO.Path]::GetTempPath()) ("seektalent-domi-install-" + [Guid]::NewGuid().ToString("N"))
  $CandidatePrefix = Join-Path $CandidateRoot "python-prefix"
  $CandidateSitePackages = Join-Path $CandidatePrefix "Lib\site-packages"
  $PreparedRuntimeDir = Join-Path $CandidateRoot "wtscli-runtime"
  $PreviousPythonPath = $env:PYTHONPATH
  try {
    New-Item -ItemType Directory -Force -Path $CandidateSitePackages | Out-Null
    New-Item -ItemType Directory -Force -Path $PreparedRuntimeDir | Out-Null
    $SeekTalentInstallSource = if ($ProductWheel) { $ProductWheel } else { "seektalent==$Version" }
    & $DomiPython -m pip install --upgrade --ignore-installed --no-cache-dir --target $CandidateSitePackages $SeekTalentInstallSource
    if ($LASTEXITCODE -ne 0) {
      Fail "seektalent_pypi_install_failed" "Failed to install seektalent==$Version with Domi Python."
    }
    Expand-Archive -Path $PreparedWtscliRuntime -DestinationPath $PreparedRuntimeDir -Force
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$CandidateSitePackages;$env:PYTHONPATH" } else { $CandidateSitePackages }
    & $DomiPython -m seektalent.domi_bootstrap `
      --package-version $Version `
      --python-path $SitePackages `
      --python-prefix-candidate $CandidatePrefix `
      --python-prefix-target $Prefix `
      --domi-python $DomiPython `
      --domi-node $DomiNode `
      --browser-bridge-bundle-dir $WtscliBundleDir `
      --browser-bridge-prepared-runtime-dir $PreparedRuntimeDir `
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
  Write-Host "固定扩展目录：~/.seektalent/chrome-extension/wtscli"
  Write-Host "Chrome 扩展目录：$env:USERPROFILE\.seektalent\chrome-extension\wtscli"
  Write-Host "打开 chrome://extensions，启用“开发者模式”，选择“加载已解压的扩展程序”，并选择上面的唯一目录。"
  Write-Host "升级后请在该页面点击 WTSCLI 的“重新加载”；若仍显示旧版本，请完全退出并重启 Chrome。"
  Write-Host "检查：seektalent browser-check"
}

if ($MyInvocation.MyCommand.Path -and $MyInvocation.InvocationName -ne ".") {
  try {
    Install-SeekTalentDomi -Version $Version -DomiPython $DomiPython -DomiNode $DomiNode -WtscliBundleDir $WtscliBundleDir -BrowserBridgeHelper $BrowserBridgeHelper -PreparedWtscliRuntime $PreparedWtscliRuntime
  } catch {
    Write-Error $_
    exit 1
  }
}
