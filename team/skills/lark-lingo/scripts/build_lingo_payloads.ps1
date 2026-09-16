param(
  [Parameter(Mandatory = $true)]
  [string]$Source,

  [Parameter(Mandatory = $true)]
  [string]$OutputDir,

  [Parameter(Mandatory = $true)]
  [string]$ClassificationId,

  [Parameter(Mandatory = $true)]
  [string]$FatherId,

  [string]$Provider = "LarkLingoImport",
  [string]$OuterIdPrefix = "lingo",
  [switch]$EscapeForCmd = $true
)

$ErrorActionPreference = "Stop"

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$raw = [System.IO.File]::ReadAllText($Source, $utf8NoBom)
$src = $raw | ConvertFrom-Json

if (-not $src.entries -or $src.entries.Count -eq 0) {
  throw "Source JSON must contain a non-empty entries array."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Get-ChildItem $OutputDir -Filter "*.json" -File -ErrorAction SilentlyContinue | Remove-Item -Force

for ($i = 0; $i -lt $src.entries.Count; $i++) {
  $entry = $src.entries[$i]
  $index = $i + 1
  $outerId = if ($entry.PSObject.Properties.Name -contains "outer_id" -and $entry.outer_id) {
    [string]$entry.outer_id
  } else {
    "{0}_{1:d3}" -f $OuterIdPrefix, $index
  }

  $aliases = @()
  if ($entry.aliases) {
    $aliases = @(
      $entry.aliases | ForEach-Object {
        @{
          key = [string]$_
          display_status = @{
            allow_highlight = $true
            allow_search = $true
          }
        }
      }
    )
  }

  $docs = @()
  if ($entry.related_docs) {
    $docs = @(
      $entry.related_docs | ForEach-Object {
        @{
          title = [string]$_.title
          url = [string]$_.url
        }
      }
    )
  }

  $payload = [ordered]@{
    main_keys = @(
      @{
        key = [string]$entry.main_key
        display_status = @{
          allow_highlight = $true
          allow_search = $true
        }
      }
    )
    aliases = $aliases
    description = [string]$entry.description
    related_meta = @{
      docs = $docs
      classifications = @(
        @{
          id = $ClassificationId
          father_id = $FatherId
        }
      )
    }
    outer_info = @{
      provider = $Provider
      outer_id = $outerId
    }
  }

  $json = $payload | ConvertTo-Json -Depth 10 -Compress
  if ($EscapeForCmd) {
    $json = $json.Replace('"', '""')
  }

  $outputFile = Join-Path $OutputDir ("{0:d3}.json" -f $index)
  [System.IO.File]::WriteAllText($outputFile, $json, $utf8NoBom)
}

Write-Output ("payload_count={0}" -f $src.entries.Count)
