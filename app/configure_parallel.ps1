# Configure Ollama to serve SmartDoc's concurrent identifier checks.
param([ValidateRange(1, 8)][int]$ParallelRequests = 5)
$ErrorActionPreference = "Stop"
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", [string]$ParallelRequests, "User")
$env:OLLAMA_NUM_PARALLEL = [string]$ParallelRequests
Write-Host "Ollama configured for up to $ParallelRequests parallel requests."
Write-Host "Restart Ollama after active summaries finish to apply this setting."
