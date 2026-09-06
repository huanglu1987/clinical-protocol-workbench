#!/bin/sh

set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: $0 /absolute/input.docx /absolute/output.pdf /absolute/record.txt" >&2
  exit 64
fi

input_docx=$1
output_pdf=$2
record_txt=$3
expected_docx_sha256=63df0e3a1240645aadde9e39b0c2f97d1f3c69ff91fe8ea6ac9e7cb7c08995e8

case "$input_docx" in
  /*.docx) ;;
  *) echo "input DOCX must be an absolute .docx path" >&2; exit 64 ;;
esac
case "$output_pdf" in
  /*.pdf) ;;
  *) echo "output PDF must be an absolute .pdf path" >&2; exit 64 ;;
esac
case "$record_txt" in
  /*.txt) ;;
  *) echo "record must be an absolute .txt path" >&2; exit 64 ;;
esac

if [ ! -f "$input_docx" ]; then
  echo "input DOCX not found" >&2
  exit 66
fi
if [ -e "$output_pdf" ] || [ -e "$record_txt" ]; then
  echo "refusing to replace an existing output or record" >&2
  exit 73
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
word_home=$(/usr/bin/osascript -e 'POSIX path of (path to home folder)')
word_temp_root="${word_home}Library/Containers/com.microsoft.Word/Data/tmp/TemporaryItems"
if [ ! -d "$word_temp_root" ]; then
  echo "Microsoft Word TemporaryItems directory not found" >&2
  exit 69
fi

mkdir -p "$(dirname -- "$output_pdf")" "$(dirname -- "$record_txt")"
temporary_pdf=$(mktemp "${word_temp_root}/s3-word-markup.XXXXXX")
temporary_pdf_with_suffix="${temporary_pdf}.pdf"
mv "$temporary_pdf" "$temporary_pdf_with_suffix"
temporary_record=$(mktemp "$(dirname -- "$record_txt")/.s3-word-record.XXXXXX")
published_pdf=0

cleanup() {
  [ ! -f "$temporary_pdf_with_suffix" ] || rm -f -- "$temporary_pdf_with_suffix"
  [ ! -f "$temporary_record" ] || rm -f -- "$temporary_record"
  if [ "$published_pdf" -eq 1 ] && [ ! -e "$record_txt" ] && [ -f "$output_pdf" ]; then
    rm -f -- "$output_pdf"
  fi
}
trap cleanup 0 1 2 15

before_hash=$(LC_ALL=C LANG=C openssl dgst -sha256 "$input_docx" | awk '{print $2}')
if [ "$before_hash" != "$expected_docx_sha256" ]; then
  echo "input DOCX hash does not match the frozen S3 fixture" >&2
  exit 1
fi

if ! /usr/bin/osascript "$script_dir/word_macos_s3_validate.applescript" \
  "$input_docx" "$temporary_pdf_with_suffix" >"$temporary_record"; then
  echo "Microsoft Word validation failed; no final evidence was published" >&2
  exit 1
fi

expected_revision_authors='临床方案 QC（待人工复核）|临床方案 QC（待人工复核）|原审阅者|原审阅者'
expected_comment_authors='临床方案 QC（待人工复核）|原审阅者'
for expected_line in \
  "opened_path=$input_docx" \
  'read_only=true' \
  'revision_count=4' \
  'comment_count=2' \
  'field_count=0'
do
  if ! LC_ALL=C LANG=C grep -Fqx "$expected_line" "$temporary_record"; then
    echo "Microsoft Word returned an unexpected validation result: $expected_line" >&2
    exit 1
  fi
done

sort_authors() {
  printf '%s\n' "$1" | tr '|' '\n' | LC_ALL=C LANG=C sort
}
actual_revision_authors=$(sed -n 's/^revision_authors=//p' "$temporary_record")
actual_comment_authors=$(sed -n 's/^comment_authors=//p' "$temporary_record")
if [ "$(sort_authors "$actual_revision_authors")" != "$(sort_authors "$expected_revision_authors")" ]; then
  echo "Microsoft Word returned an unexpected revision author distribution" >&2
  exit 1
fi
if [ "$(sort_authors "$actual_comment_authors")" != "$(sort_authors "$expected_comment_authors")" ]; then
  echo "Microsoft Word returned an unexpected comment author distribution" >&2
  exit 1
fi

after_hash=$(LC_ALL=C LANG=C openssl dgst -sha256 "$input_docx" | awk '{print $2}')
if [ "$before_hash" != "$after_hash" ]; then
  echo "input DOCX hash changed; refusing output" >&2
  exit 1
fi
if [ ! -s "$temporary_pdf_with_suffix" ] || ! head -c 5 "$temporary_pdf_with_suffix" | grep -q '^%PDF-'; then
  echo "Microsoft Word did not create a valid PDF header" >&2
  exit 1
fi

pdf_hash=$(LC_ALL=C LANG=C openssl dgst -sha256 "$temporary_pdf_with_suffix" | awk '{print $2}')
printf 'automation_status=passed\nvisual_status=pending_manual_page_review\ndocx_sha256_before=%s\ndocx_sha256_after=%s\npdf_sha256=%s\n' \
  "$before_hash" "$after_hash" "$pdf_hash" >>"$temporary_record"
mv "$temporary_pdf_with_suffix" "$output_pdf"
published_pdf=1
mv "$temporary_record" "$record_txt"
published_pdf=0
trap - 0 1 2 15

echo "$output_pdf"
