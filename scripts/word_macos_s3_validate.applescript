on joinText(itemsList, separatorText)
	set AppleScript's text item delimiters to separatorText
	set resultText to itemsList as text
	set AppleScript's text item delimiters to ""
	return resultText
end joinText

on run argv
	if (count of argv) is not 2 then error "expected DOCX and PDF paths"
	set inputPath to item 1 of argv
	set pdfPath to item 2 of argv
	set wordTemporaryRoot to (POSIX path of (path to home folder)) & "Library/Containers/com.microsoft.Word/Data/tmp/TemporaryItems/"
	if pdfPath does not start with wordTemporaryRoot then error "pdf_path_must_be_in_word_temporary_items"
	set openedName to missing value
	set revisionAuthors to {}
	set commentAuthors to {}
	with timeout of 900 seconds
		tell application "Microsoft Word"
			set priorAlerts to display alerts
			set priorSecurity to automation security
			try
				set display alerts to alerts all
				set automation security to msoAutomationSecurityForceDisable
				open file name inputPath confirm conversions false read only true add to recent files false repair false
				set openedName to name of active document
				if (posix full name of document openedName) is not inputPath then error "opened_unexpected_document"
				set revisionList to get every revision of document openedName
				repeat with reviewItem in revisionList
					set end of revisionAuthors to (author of reviewItem as text)
				end repeat
				set commentList to get every Word comment of document openedName
				repeat with commentItem in commentList
					set end of commentAuthors to (author of commentItem as text)
				end repeat
				set fieldCount to count of fields of document openedName
				set print revisions of document openedName to true
				set show revisions of document openedName to true
				set documentView to view of active window
				set view type of documentView to print view
				set revisions view of documentView to revisions view final
				set revisions mode of documentView to mixed revisions
				set show revisions and comments of documentView to true
				set show insertions and deletions of documentView to true
				set show comments of documentView to true
				repaginate document openedName
				save as document openedName file name pdfPath file format format PDF add to recent files false
				set openedPath to posix full name of document openedName
				set readOnlyState to read only of document openedName
				set revisionCount to count of revisionList
				set commentCount to count of commentList
				set automation security to priorSecurity
				set display alerts to priorAlerts
				return "opened_path=" & openedPath & linefeed & ¬
					"read_only=" & readOnlyState & linefeed & ¬
					"revision_count=" & revisionCount & linefeed & ¬
					"comment_count=" & commentCount & linefeed & ¬
					"field_count=" & fieldCount & linefeed & ¬
					"revision_authors=" & my joinText(revisionAuthors, "|") & linefeed & ¬
					"comment_authors=" & my joinText(commentAuthors, "|") & linefeed & ¬
					"close_status=left_open_read_only_due_word_applescript_close_limit"
			on error errorMessage number errorNumber
				set automation security to priorSecurity
				set display alerts to priorAlerts
				error errorMessage number errorNumber
			end try
		end tell
	end timeout
end run
