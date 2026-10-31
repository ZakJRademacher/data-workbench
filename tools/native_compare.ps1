param([Parameter(Mandatory=$true)][string]$SourceFolder, [Parameter(Mandatory=$true)][string]$OutputFolder, [Parameter(Mandatory=$true)][string]$WorkbookTemplate)
$ErrorActionPreference = 'Stop'
$OutputFolder = [IO.Path]::GetFullPath($OutputFolder)
New-Item -ItemType Directory -Path $OutputFolder -Force | Out-Null
$copy = Join-Path $OutputFolder 'Native Excel Check.xlsx'
Copy-Item -LiteralPath $WorkbookTemplate -Destination $copy
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.AskToUpdateLinks = $false
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ExcelProcessLookup { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); }'
$excelProcessId = [uint32]0
[ExcelProcessLookup]::GetWindowThreadProcessId([IntPtr]$excel.Hwnd, [ref]$excelProcessId) | Out-Null
$book = $null
try {
    $book = $excel.Workbooks.Open($copy, 0, $false)
    $book.Worksheets.Item('Start here').Range('B8').Value2 = $SourceFolder
    foreach ($connection in $book.Connections) {
        try { $connection.OLEDBConnection.BackgroundQuery = $false } catch { }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $book.RefreshAll()
    $excel.CalculateUntilAsyncQueriesDone()
    $timer.Stop()
    $excelProcess = [Diagnostics.Process]::GetProcessById($excelProcessId)
    $peakExcelMemory = [Math]::Round($excelProcess.PeakWorkingSet64 / 1MB, 1)
    $results = @{}
    foreach ($definition in @(@('Clean master','CleanMaster'),@('Needs review','NeedsReview'),@('Import history','ImportHistory'))) {
        $table = $book.Worksheets.Item($definition[0]).ListObjects.Item($definition[1])
        $headers = @(); for ($column=1; $column -le $table.ListColumns.Count; $column++) { $headers += [string]$table.HeaderRowRange.Cells.Item(1,$column).Value2 }
        $rows = @()
        if ($null -ne $table.DataBodyRange) {
            $values = $table.DataBodyRange.Value2
            for ($row=1; $row -le $table.DataBodyRange.Rows.Count; $row++) {
                $record = [ordered]@{}
                for ($column=1; $column -le $headers.Count; $column++) {
                    $value = $values[$row,$column]
                    if ($headers[$column-1] -in @('OrderDate','ShipDate') -and $null -ne $value) { $value = [DateTime]::FromOADate([double]$value).ToString('yyyy-MM-dd') }
                    if ($headers[$column-1] -eq 'Sales' -and $null -ne $value) { $value = ([decimal]$value).ToString('0.0000',[Globalization.CultureInfo]::InvariantCulture) }
                    $record[$headers[$column-1]] = $value
                }
                $rows += $record
            }
        }
        $results[$definition[1]] = $rows
    }
    $book.Save()
    @{passed=$true; elapsed_seconds=$timer.Elapsed.TotalSeconds; peak_excel_process_rss_mb=$peakExcelMemory; memory_scope='Excel process peak working set through refresh; separate Power Query container processes are not included'; tables=$results; excel_version=$excel.Version; native_workbook=$copy} | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath (Join-Path $OutputFolder 'native-results.json') -Encoding UTF8
    Write-Output ('Native Excel refresh completed in ' + $timer.Elapsed.TotalSeconds + ' seconds.')
} finally {
    if ($null -ne $book) { $book.Close($false) }
    $excel.Quit()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
