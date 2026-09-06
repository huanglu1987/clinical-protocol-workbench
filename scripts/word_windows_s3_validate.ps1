[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DocxPath,

    [Parameter(Mandatory = $true)]
    [string]$PdfPath,

    [Parameter(Mandatory = $true)]
    [string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$wdAlertsAll = -1
$wdDoNotSaveChanges = 0
$wdPrintView = 3
$wdRevisionsViewFinal = 0
$wdBalloonRevisions = 0
$wdExportFormatPdf = 17
$wdExportOptimizeForPrint = 0
$wdExportAllDocument = 0
$wdExportDocumentWithMarkup = 7
$wdExportCreateHeadingBookmarks = 1
$msoAutomationSecurityForceDisable = 3
$missing = [Type]::Missing
$expectedRevisionCount = 4
$expectedCommentCount = 2
$expectedDocxSha256 = "63df0e3a1240645aadde9e39b0c2f97d1f3c69ff91fe8ea6ac9e7cb7c08995e8"
$existingReviewAuthor = -join [char[]]@(0x539F, 0x5BA1, 0x9605, 0x8005)
$newReviewAuthor = -join [char[]]@(
    0x4E34, 0x5E8A, 0x65B9, 0x6848, 0x20, 0x51, 0x43,
    0xFF08, 0x5F85, 0x4EBA, 0x5DE5, 0x590D, 0x6838, 0xFF09
)
$expectedRevisionAuthorCounts = [ordered]@{}
$expectedRevisionAuthorCounts[$existingReviewAuthor] = 2
$expectedRevisionAuthorCounts[$newReviewAuthor] = 2
$expectedCommentAuthorCounts = [ordered]@{}
$expectedCommentAuthorCounts[$existingReviewAuthor] = 1
$expectedCommentAuthorCounts[$newReviewAuthor] = 1

function Test-FullyQualifiedWindowsPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    return $Path -match '^(?:[A-Za-z]:[\\/]|\\\\(?:\?\\)?[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

function Release-ComObject {
    param([object]$ComObject)

    if ($null -ne $ComObject -and [Runtime.InteropServices.Marshal]::IsComObject($ComObject)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($ComObject)
    }
}

function Get-Sha256 {
    param([string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-PdfHeader {
    param([string]$Path)

    $stream = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        if ($stream.Length -lt 5) {
            return $false
        }
        $buffer = New-Object byte[] 5
        [void]$stream.Read($buffer, 0, $buffer.Length)
        return [Text.Encoding]::ASCII.GetString($buffer) -eq "%PDF-"
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Write-TextAtomically {
    param(
        [string]$Path,
        [string]$Text
    )

    $directory = [IO.Path]::GetDirectoryName($Path)
    $temporaryPath = [IO.Path]::Combine(
        $directory,
        ".{0}.{1}.tmp" -f [IO.Path]::GetFileName($Path), [Guid]::NewGuid().ToString("N")
    )
    try {
        [IO.File]::WriteAllText($temporaryPath, $Text, [System.Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Replace($temporaryPath, $Path, $null)
        }
        else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if ([IO.File]::Exists($temporaryPath)) {
            [IO.File]::Delete($temporaryPath)
        }
    }
}

$failures = New-Object 'System.Collections.Generic.List[string]'
$warnings = New-Object 'System.Collections.Generic.List[string]'
$revisionItems = New-Object 'System.Collections.Generic.List[object]'
$commentItems = New-Object 'System.Collections.Generic.List[object]'
$revisionAuthorCounts = [ordered]@{}
$commentAuthorCounts = [ordered]@{}

$word = $null
$documents = $null
$document = $null
$documentWindows = $null
$window = $null
$view = $null
$revisions = $null
$comments = $null
$priorDisplayAlerts = $null
$priorAutomationSecurity = $null

$inputFullPath = $null
$outputFullPath = $null
$reportFullPath = $null
$reportTargetAvailable = $false
$temporaryPdfPath = $null
$pdfTargetPreexisted = $false
$pdfPublished = $false
$pdfBackupPath = $null
$finalPdfSha256 = $null
$sourceSha256Before = $null
$sourceSha256After = $null
$wordVersion = $null
$wordBuild = $null
$openedFullName = $null
$readOnly = $false
$repaginated = $false
$closedWithoutSaving = $false
$wordQuitWithoutSaving = $false
$pdfExists = $false
$pdfLength = 0
$pdfSha256 = $null
$pdfHeaderValid = $false
$markupState = [ordered]@{
    document_print_revisions = $false
    view_show_revisions_and_comments = $false
    view_show_insertions_and_deletions = $false
    view_show_comments = $false
    view_show_format_changes = $false
    view_revisions_view = $null
    view_revisions_mode = $null
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "This validator must run on Windows with desktop Microsoft Word installed."
    }
    if (-not (Test-FullyQualifiedWindowsPath -Path $ReportPath)) {
        throw "ReportPath must be a fully qualified Windows path."
    }
    $reportFullPath = [IO.Path]::GetFullPath($ReportPath)
    if (-not [string]::Equals([IO.Path]::GetExtension($reportFullPath), ".json", [StringComparison]::OrdinalIgnoreCase)) {
        throw "ReportPath must have a .json extension."
    }
    if ([IO.File]::Exists($reportFullPath)) {
        throw "ReportPath already exists; choose a new versioned evidence path."
    }
    $reportTargetAvailable = $true
    $reportDirectory = [IO.Path]::GetDirectoryName($reportFullPath)
    if ([string]::IsNullOrWhiteSpace($reportDirectory)) {
        throw "ReportPath must include an absolute parent directory."
    }
    if (-not [IO.Directory]::Exists($reportDirectory)) {
        [void][IO.Directory]::CreateDirectory($reportDirectory)
    }

    if (-not (Test-FullyQualifiedWindowsPath -Path $DocxPath)) {
        throw "DocxPath must be a fully qualified Windows path."
    }
    if (-not (Test-FullyQualifiedWindowsPath -Path $PdfPath)) {
        throw "PdfPath must be a fully qualified Windows path."
    }

    $inputFullPath = [IO.Path]::GetFullPath($DocxPath)
    $outputFullPath = [IO.Path]::GetFullPath($PdfPath)
    if (-not [string]::Equals([IO.Path]::GetExtension($inputFullPath), ".docx", [StringComparison]::OrdinalIgnoreCase)) {
        throw "DocxPath must have a .docx extension."
    }
    if (-not [string]::Equals([IO.Path]::GetExtension($outputFullPath), ".pdf", [StringComparison]::OrdinalIgnoreCase)) {
        throw "PdfPath must have a .pdf extension."
    }
    if ([IO.File]::Exists($outputFullPath)) {
        throw "PdfPath already exists; choose a new versioned evidence path."
    }
    if ([string]::Equals($inputFullPath, $outputFullPath, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($inputFullPath, $reportFullPath, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($outputFullPath, $reportFullPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "DocxPath, PdfPath, and ReportPath must be different paths."
    }
    if (-not [IO.File]::Exists($inputFullPath)) {
        throw "Input DOCX does not exist: $inputFullPath"
    }

    $outputDirectory = [IO.Path]::GetDirectoryName($outputFullPath)
    if ([string]::IsNullOrWhiteSpace($outputDirectory)) {
        throw "PdfPath must include an absolute parent directory."
    }
    if (-not [IO.Directory]::Exists($outputDirectory)) {
        [void][IO.Directory]::CreateDirectory($outputDirectory)
    }
    $pdfTargetPreexisted = [IO.File]::Exists($outputFullPath)
    $temporaryPdfPath = [IO.Path]::Combine(
        $outputDirectory,
        ".{0}.{1}.tmp.pdf" -f [IO.Path]::GetFileNameWithoutExtension($outputFullPath), [Guid]::NewGuid().ToString("N")
    )

    $sourceSha256Before = Get-Sha256 -Path $inputFullPath
    if ($sourceSha256Before -ne $expectedDocxSha256) {
        throw "DocxPath SHA-256 does not match the frozen S3 fixture."
    }

    $word = New-Object -ComObject Word.Application
    $wordVersion = [string]$word.Version
    $wordBuild = [string]$word.Build
    $priorDisplayAlerts = $word.DisplayAlerts
    $priorAutomationSecurity = $word.AutomationSecurity
    $word.DisplayAlerts = $wdAlertsAll
    $word.AutomationSecurity = $msoAutomationSecurityForceDisable
    $word.Visible = $true

    $documents = $word.Documents
    # Pass OpenAndRepair explicitly; the preceding optional arguments follow
    # the Documents.Open COM signature documented by Microsoft.
    $document = $documents.Open(
        $inputFullPath,
        $false,
        $true,
        $false,
        $missing,
        $missing,
        $missing,
        $missing,
        $missing,
        $missing,
        $missing,
        $true,
        $false,
        $false
    )

    $openedFullName = [IO.Path]::GetFullPath([string]$document.FullName)
    if (-not [string]::Equals($openedFullName, $inputFullPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Microsoft Word opened an unexpected document: $openedFullName"
    }
    $readOnly = [bool]$document.ReadOnly
    if (-not $readOnly) {
        throw "Microsoft Word did not open the DOCX read-only."
    }

    $revisions = $document.Revisions
    for ($index = 1; $index -le $revisions.Count; $index++) {
        $revision = $null
        try {
            $revision = $revisions.Item($index)
            [void]$revisionItems.Add([ordered]@{
                index = $index
                author = [string]$revision.Author
            })
        }
        finally {
            Release-ComObject -ComObject $revision
        }
    }

    $comments = $document.Comments
    for ($index = 1; $index -le $comments.Count; $index++) {
        $comment = $null
        try {
            $comment = $comments.Item($index)
            [void]$commentItems.Add([ordered]@{
                index = $index
                author = [string]$comment.Author
            })
        }
        finally {
            Release-ComObject -ComObject $comment
        }
    }

    if ($revisionItems.Count -eq 0) {
        throw "The S3 DOCX contains no revisions."
    }
    if ($commentItems.Count -eq 0) {
        throw "The S3 DOCX contains no comments."
    }
    if (@($revisionItems | Where-Object { [string]::IsNullOrWhiteSpace($_.author) }).Count -gt 0) {
        throw "At least one revision has no author."
    }
    if (@($commentItems | Where-Object { [string]::IsNullOrWhiteSpace($_.author) }).Count -gt 0) {
        throw "At least one comment has no author."
    }

    foreach ($item in $revisionItems) {
        if (-not $revisionAuthorCounts.Contains($item.author)) {
            $revisionAuthorCounts[$item.author] = 0
        }
        $revisionAuthorCounts[$item.author]++
    }
    foreach ($item in $commentItems) {
        if (-not $commentAuthorCounts.Contains($item.author)) {
            $commentAuthorCounts[$item.author] = 0
        }
        $commentAuthorCounts[$item.author]++
    }
    if ($revisionItems.Count -ne $expectedRevisionCount -or
        $revisionAuthorCounts.Count -ne 2 -or
        $revisionAuthorCounts[$existingReviewAuthor] -ne 2 -or
        $revisionAuthorCounts[$newReviewAuthor] -ne 2) {
        throw "Revision count or author distribution does not match the frozen S3 fixture (4 total; 2 existing-review author; 2 new-review author)."
    }
    if ($commentItems.Count -ne $expectedCommentCount -or
        $commentAuthorCounts.Count -ne 2 -or
        $commentAuthorCounts[$existingReviewAuthor] -ne 1 -or
        $commentAuthorCounts[$newReviewAuthor] -ne 1) {
        throw "Comment count or author distribution does not match the frozen S3 fixture (2 total; one per expected author)."
    }

    $documentWindows = $document.Windows
    if ($documentWindows.Count -lt 1) {
        throw "Microsoft Word did not create a document window for markup display validation."
    }
    $window = $documentWindows.Item(1)
    $view = $window.View
    $view.Type = $wdPrintView
    $view.RevisionsView = $wdRevisionsViewFinal
    $view.RevisionsMode = $wdBalloonRevisions
    $view.ShowRevisionsAndComments = $true
    $view.ShowInsertionsAndDeletions = $true
    $view.ShowComments = $true
    $view.ShowFormatChanges = $true
    $document.PrintRevisions = $true

    $markupState.document_print_revisions = [bool]$document.PrintRevisions
    $markupState.view_show_revisions_and_comments = [bool]$view.ShowRevisionsAndComments
    $markupState.view_show_insertions_and_deletions = [bool]$view.ShowInsertionsAndDeletions
    $markupState.view_show_comments = [bool]$view.ShowComments
    $markupState.view_show_format_changes = [bool]$view.ShowFormatChanges
    $markupState.view_revisions_view = [int]$view.RevisionsView
    $markupState.view_revisions_mode = [int]$view.RevisionsMode

    if (-not $markupState.document_print_revisions -or
        -not $markupState.view_show_revisions_and_comments -or
        -not $markupState.view_show_insertions_and_deletions -or
        -not $markupState.view_show_comments -or
        -not $markupState.view_show_format_changes) {
        throw "Microsoft Word did not retain the requested markup display and print settings."
    }

    $document.Repaginate()
    $repaginated = $true
    $document.ExportAsFixedFormat(
        $temporaryPdfPath,
        $wdExportFormatPdf,
        $false,
        $wdExportOptimizeForPrint,
        $wdExportAllDocument,
        1,
        1,
        $wdExportDocumentWithMarkup,
        $true,
        $true,
        $wdExportCreateHeadingBookmarks,
        $true,
        $true,
        $false
    )

    $pdfExists = [IO.File]::Exists($temporaryPdfPath)
    if (-not $pdfExists) {
        throw "Microsoft Word did not create the requested PDF."
    }
    $pdfLength = (Get-Item -LiteralPath $temporaryPdfPath).Length
    $pdfHeaderValid = Test-PdfHeader -Path $temporaryPdfPath
    if ($pdfLength -le 0 -or -not $pdfHeaderValid) {
        throw "The exported file is not a non-empty PDF."
    }
    $pdfSha256 = Get-Sha256 -Path $temporaryPdfPath
}
catch {
    [void]$failures.Add($_.Exception.Message)
}
finally {
    if ($null -ne $document) {
        try {
            $document.Close($wdDoNotSaveChanges)
            $closedWithoutSaving = $true
        }
        catch {
            [void]$failures.Add("Failed to close the DOCX without saving: $($_.Exception.Message)")
        }
    }

    if ($null -ne $word) {
        try {
            if ($null -ne $priorAutomationSecurity) {
                $word.AutomationSecurity = $priorAutomationSecurity
            }
            if ($null -ne $priorDisplayAlerts) {
                $word.DisplayAlerts = $priorDisplayAlerts
            }
            $word.Quit($wdDoNotSaveChanges)
            $wordQuitWithoutSaving = $true
        }
        catch {
            [void]$failures.Add("Failed to quit Microsoft Word without saving: $($_.Exception.Message)")
        }
    }

    foreach ($item in @($view, $window, $documentWindows, $comments, $revisions, $document, $documents, $word)) {
        try {
            Release-ComObject -ComObject $item
        }
        catch {
            [void]$warnings.Add("COM release warning: $($_.Exception.Message)")
        }
    }

    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()

    if ($null -ne $inputFullPath -and [IO.File]::Exists($inputFullPath)) {
        try {
            $sourceSha256After = Get-Sha256 -Path $inputFullPath
            if ($null -ne $sourceSha256Before -and $sourceSha256After -ne $sourceSha256Before) {
                [void]$failures.Add("The source DOCX SHA-256 changed during validation.")
            }
        }
        catch {
            [void]$failures.Add("Failed to calculate the source DOCX SHA-256 after validation: $($_.Exception.Message)")
        }
    }
    elseif ($null -ne $sourceSha256Before) {
        [void]$failures.Add("The source DOCX is missing after validation.")
    }
}

if ($null -ne $openedFullName -and -not $closedWithoutSaving) {
    [void]$failures.Add("The DOCX was not confirmed closed with wdDoNotSaveChanges.")
}
if ($null -ne $wordVersion -and -not $wordQuitWithoutSaving) {
    [void]$failures.Add("Microsoft Word was not confirmed quit with wdDoNotSaveChanges.")
}

$revisionAuthors = @($revisionItems | ForEach-Object { $_.author } | Sort-Object -Unique)
$commentAuthors = @($commentItems | ForEach-Object { $_.author } | Sort-Object -Unique)
$sourceUnchanged = (
    $null -ne $sourceSha256Before -and
    $null -ne $sourceSha256After -and
    $sourceSha256Before -eq $sourceSha256After
)

if ($failures.Count -eq 0 -and $sourceUnchanged -and $null -ne $temporaryPdfPath) {
    try {
        if ($pdfTargetPreexisted) {
            $pdfBackupPath = [IO.Path]::Combine(
                [IO.Path]::GetDirectoryName($outputFullPath),
                ".{0}.{1}.backup.pdf" -f [IO.Path]::GetFileNameWithoutExtension($outputFullPath), [Guid]::NewGuid().ToString("N")
            )
            [IO.File]::Replace($temporaryPdfPath, $outputFullPath, $pdfBackupPath)
        }
        else {
            [IO.File]::Move($temporaryPdfPath, $outputFullPath)
        }
        $pdfPublished = $true
        $finalPdfSha256 = Get-Sha256 -Path $outputFullPath
        if ($finalPdfSha256 -ne $pdfSha256 -or -not (Test-PdfHeader -Path $outputFullPath)) {
            throw "The atomically published PDF does not match the validated temporary PDF."
        }
    }
    catch {
        [void]$failures.Add("Failed to atomically publish the validated PDF: $($_.Exception.Message)")
    }
}

if ($failures.Count -gt 0 -and $pdfPublished) {
    try {
        if ($pdfTargetPreexisted -and $null -ne $pdfBackupPath -and [IO.File]::Exists($pdfBackupPath)) {
            [IO.File]::Replace($pdfBackupPath, $outputFullPath, $null)
        }
        elseif (-not $pdfTargetPreexisted -and [IO.File]::Exists($outputFullPath)) {
            [IO.File]::Delete($outputFullPath)
        }
        $pdfPublished = $false
        $finalPdfSha256 = $null
    }
    catch {
        [void]$failures.Add("Failed to roll back the PDF after publication validation failed: $($_.Exception.Message)")
    }
}

if (-not $pdfPublished -and $null -ne $temporaryPdfPath -and [IO.File]::Exists($temporaryPdfPath)) {
    try {
        [IO.File]::Delete($temporaryPdfPath)
    }
    catch {
        [void]$warnings.Add("Temporary PDF cleanup warning: $($_.Exception.Message)")
    }
}

$automationStatus = if ($failures.Count -eq 0 -and $sourceUnchanged -and $pdfPublished) { "passed" } else { "failed" }
$status = if ($automationStatus -eq "passed") { "pending_manual_visual_review" } else { "failed" }

$result = [ordered]@{
    schema_version = "clinical-protocol-workbench.s3-word-windows.v1"
    status = $status
    automation_status = $automationStatus
    runtime = [ordered]@{
        platform = [Environment]::OSVersion.ToString()
        powershell_version = $PSVersionTable.PSVersion.ToString()
        microsoft_word_version = $wordVersion
        microsoft_word_build = $wordBuild
    }
    input = [ordered]@{
        docx_path = $inputFullPath
        expected_sha256 = $expectedDocxSha256
        sha256_before = $sourceSha256Before
        sha256_after = $sourceSha256After
        sha256_unchanged = $sourceUnchanged
        opened_full_name = $openedFullName
        opened_read_only = $readOnly
    }
    review = [ordered]@{
        revision_count_scope = "Document.Revisions (main story)"
        expected_revision_count = $expectedRevisionCount
        expected_revision_author_counts = $expectedRevisionAuthorCounts
        revision_count = $revisionItems.Count
        revision_authors = $revisionAuthors
        revision_author_counts = $revisionAuthorCounts
        revisions = @($revisionItems)
        expected_comment_count = $expectedCommentCount
        expected_comment_author_counts = $expectedCommentAuthorCounts
        comment_count = $commentItems.Count
        comment_authors = $commentAuthors
        comment_author_counts = $commentAuthorCounts
        comments = @($commentItems)
        markup = $markupState
        repaginated = $repaginated
    }
    output = [ordered]@{
        pdf_path = $outputFullPath
        target_preexisted = $pdfTargetPreexisted
        published_this_run = $pdfPublished
        exists_after_run = ($null -ne $outputFullPath -and [IO.File]::Exists($outputFullPath))
        byte_length = $pdfLength
        sha256 = $finalPdfSha256
        pdf_header_valid = $pdfHeaderValid
        export_item = "wdExportDocumentWithMarkup"
    }
    visual_review = [ordered]@{
        status = if ($automationStatus -eq "passed") { "pending_manual_page_review" } else { "not_reached" }
        required_checks = @(
            "insertions and deletions visible",
            "new and existing comment balloons visible",
            "table and footer intact",
            "no clipping, overlap, missing glyphs, or abnormal pagination"
        )
    }
    report_path = $reportFullPath
    safety = [ordered]@{
        requested_open_and_repair = $false
        added_to_recent_files = $false
        requested_alert_level = "wdAlertsAll"
        word_visible_during_validation = $true
        closed_with_wd_do_not_save_changes = $closedWithoutSaving
        word_quit_with_wd_do_not_save_changes = $wordQuitWithoutSaving
        accept_or_reject_revision_calls = 0
        script_explicit_network_calls = 0
        system_level_network_monitoring = "not_performed"
    }
    errors = @($failures)
    warnings = @($warnings)
}

$json = $result | ConvertTo-Json -Depth 8 -Compress
if ($reportTargetAvailable -and $null -ne $reportFullPath) {
    try {
        Write-TextAtomically -Path $reportFullPath -Text ($json + [Environment]::NewLine)
    }
    catch {
        [void]$failures.Add("Failed to atomically write ReportPath: $($_.Exception.Message)")
        if ($pdfPublished) {
            try {
                if ($pdfTargetPreexisted -and $null -ne $pdfBackupPath -and [IO.File]::Exists($pdfBackupPath)) {
                    [IO.File]::Replace($pdfBackupPath, $outputFullPath, $null)
                }
                elseif (-not $pdfTargetPreexisted -and [IO.File]::Exists($outputFullPath)) {
                    [IO.File]::Delete($outputFullPath)
                }
                $pdfPublished = $false
                $finalPdfSha256 = $null
            }
            catch {
                [void]$failures.Add("Failed to roll back the published PDF after ReportPath failure: $($_.Exception.Message)")
            }
        }
        $automationStatus = "failed"
        $status = "failed"
        $result["status"] = $status
        $result["automation_status"] = $automationStatus
        $result["visual_review"]["status"] = "not_reached"
        $result["output"]["published_this_run"] = $pdfPublished
        $result["output"]["exists_after_run"] = ($null -ne $outputFullPath -and [IO.File]::Exists($outputFullPath))
        $result["output"]["sha256"] = $finalPdfSha256
        $result["errors"] = @($failures)
        $json = $result | ConvertTo-Json -Depth 8 -Compress
    }
}

if ($automationStatus -eq "passed" -and $null -ne $pdfBackupPath -and [IO.File]::Exists($pdfBackupPath)) {
    try {
        [IO.File]::Delete($pdfBackupPath)
    }
    catch {
        [void]$warnings.Add("Published PDF backup cleanup warning: $($_.Exception.Message)")
    }
}

$json
if ($automationStatus -ne "passed") {
    exit 1
}
exit 0
