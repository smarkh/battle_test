param([Parameter(Mandatory)][string]$In, [Parameter(Mandatory)][string]$Out)
# Converts a .docx (Word) or .pptx (PowerPoint) to PDF with the installed Office apps.
# PowerPoint runs as one shared copy, so if you already have it open this attaches
# to yours: it closes only the file it opened, and quits only if nothing else is open.
$In = (Resolve-Path $In).Path
if ($In -like '*.docx') {
    $word = New-Object -ComObject Word.Application   # Word gets its own hidden copy
    $word.Visible = $false
    try {
        $d = $word.Documents.Open($In, $false, $true)
        $d.SaveAs([ref]$Out, [ref]17)
        $d.Close([ref]0)
    } finally { if ($word.Documents.Count -eq 0) { $word.Quit() } }
} elseif ($In -like '*.pptx') {
    $pp = New-Object -ComObject PowerPoint.Application
    $p = $null
    try {
        $p = $pp.Presentations.Open($In, $true, $false, $false)
        $p.SaveAs($Out, 32)
    } finally {
        if ($p) { $p.Close() }
        if ($pp.Presentations.Count -eq 0) { $pp.Quit() }
    }
}
"wrote $Out"
