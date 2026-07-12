param(
  [string]$Version = "0.7.42",
  [string]$DomiPython = "",
  [string]$DomiNode = ""
)

function Fail($ReasonCode, $Message) {
  throw "reason_code=$ReasonCode $Message"
}

function Install-SeekTalentDomi {
  param(
    [string]$Version = "0.7.42",
    [string]$DomiPython = "",
    [string]$DomiNode = ""
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

  $Prefix = Join-Path $env:USERPROFILE ".seektalent\python-prefix\$Version"
  $SitePackages = Join-Path $Prefix "Lib\site-packages"
  $BinDir = Join-Path $env:USERPROFILE ".seektalent\bin"
  New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null
  New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

  & $DomiPython -m pip install --upgrade --ignore-installed --no-cache-dir --target $SitePackages "seektalent==$Version"
  if ($LASTEXITCODE -ne 0) {
    Fail "seektalent_pypi_install_failed" "Failed to install seektalent==$Version with Domi Python."
  }

  $PreviousPythonPath = $env:PYTHONPATH
  try {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$SitePackages;$env:PYTHONPATH" } else { $SitePackages }
    & $DomiPython -m seektalent.domi_bootstrap `
      --package-version $Version `
      --python-path $SitePackages `
      --domi-python $DomiPython `
      --domi-node $DomiNode `
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
  }

  if (($env:Path -split ";") -notcontains $BinDir) {
    $env:Path = "$BinDir;$env:Path"
  }

  Write-Host "SeekTalent Domi install ready. Run: seektalent workbench"
}

if ($MyInvocation.MyCommand.Path -and $MyInvocation.InvocationName -ne ".") {
  try {
    Install-SeekTalentDomi -Version $Version -DomiPython $DomiPython -DomiNode $DomiNode
  } catch {
    Write-Error $_
    exit 1
  }
}
