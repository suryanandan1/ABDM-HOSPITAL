# Downloads a portable Java runtime, the official HL7 FHIR validator and the ABDM (NRCES) IG package
# into backend/tools. Nothing is installed system-wide. Run from the backend folder:
#     powershell -ExecutionPolicy Bypass -File scripts\setup_validator.ps1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$tools = Join-Path $PSScriptRoot "..\tools"
New-Item -ItemType Directory -Force $tools | Out-Null

if (-not (Get-ChildItem $tools -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue)) {
    Write-Host "Downloading Java 21 runtime (Eclipse Temurin)..."
    $zip = Join-Path $tools "jre.zip"
    Invoke-WebRequest "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse" -OutFile $zip
    Expand-Archive $zip -DestinationPath $tools
    Remove-Item $zip
}
if (-not (Test-Path (Join-Path $tools "validator_cli.jar"))) {
    Write-Host "Downloading HL7 FHIR validator..."
    Invoke-WebRequest "https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar" -OutFile (Join-Path $tools "validator_cli.jar")
}
# The IG package is fetched by the validator itself on first run (cached in %USERPROFILE%\.fhir\packages);
# a copy is kept here so the profiles can be inspected offline.
if (-not (Test-Path (Join-Path $tools "ndhm.in-6.5.0.tgz"))) {
    Write-Host "Downloading ABDM IG package ndhm.in 6.5.0..."
    Invoke-WebRequest "https://packages.simplifier.net/ndhm.in/6.5.0" -OutFile (Join-Path $tools "ndhm.in-6.5.0.tgz")
}
Write-Host "Validator tools ready in $tools"
