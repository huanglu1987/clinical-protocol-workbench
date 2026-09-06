import io
import unittest
import warnings
import zipfile
from dataclasses import replace

from lxml import etree

from clinical_qc.docx_package import (COMMENTS, CT, DOC_RELS, MAIN, NS, R, W, DocxPackage,
                                     Limits, RejectedDocument, digest, w)
from clinical_qc.redline import (AUTHOR, Change, freeze_plan, generate, make_anchor,
                                visible_text)
from fixtures import make_docx, pack, paragraph

WHEN = "2026-09-03T01:02:03Z"


class NativeReviewTests(unittest.TestCase):
    def plan(self, source, *, index=0, before=",", after="，", action="replace",
             rule="chinese_sentence_comma", comment="", confirmed=True):
        text = visible_text(list(DocxPackage(source).xml(MAIN).iter(w("p")))[index])
        start = text.index(before)
        anchor = make_anchor(source, index, start, start + len(before))
        change = Change("F001", anchor, action, after, rule, comment, confirmed)
        return freeze_plan(digest(source), (change,), WHEN)

    def assertReject(self, code, callable_, *args, **kwargs):
        with self.assertRaisesRegex(RejectedDocument, "^" + code + "$"):
            callable_(*args, **kwargs)

    def test_native_replace_preserves_history_source_and_unmodified_parts(self):
        source = make_docx(paragraph("完成访视,记录结果。"), history=True)
        original = source[:]
        result = generate(source, self.plan(source))
        before, after = DocxPackage(source), DocxPackage(result.content)
        self.assertEqual(source, original)
        doc = after.xml(MAIN)
        self.assertEqual(result.revision_ids, ("13", "14"))
        self.assertEqual(doc.xpath(".//w:del[@w:id='13']/w:r/w:delText/text()", namespaces=NS), [","])
        self.assertEqual(doc.xpath(".//w:ins[@w:id='14']/w:r/w:t/text()", namespaces=NS), ["，"])
        self.assertEqual(doc.xpath(".//w:ins[@w:id='14']/w:r/w:rPr/w:color/@w:val", namespaces=NS), ["123456"])
        self.assertEqual(after.parts[COMMENTS], before.parts[COMMENTS])
        for name in before.parts:
            if name != MAIN:
                self.assertEqual(after.parts[name], before.parts[name], name)
        self.assertEqual(visible_text(doc), "完成访视，记录结果。待接受文字已有批注位置")
        self.assertEqual(result.word_visual_status, "not_performed")
        self.assertEqual(result.output_sha256, digest(result.content))

    def test_minimal_duplicate_comma_only_deletes_extra_character(self):
        source = make_docx(paragraph("完成访视，，记录结果。"))
        plan = self.plan(source, before="，，", after="，", rule="duplicate_chinese_comma")
        result = generate(source, plan)
        doc = DocxPackage(result.content).xml(MAIN)
        self.assertEqual(len(result.revision_ids), 1)
        self.assertEqual(doc.xpath(".//w:del/w:r/w:delText/text()", namespaces=NS), ["，"])
        self.assertEqual(visible_text(doc), "完成访视，记录结果。")
        self.assertEqual(visible_text(doc, original=True), "完成访视，，记录结果。")

    def test_confirmed_burning_sensation_term_is_a_native_replacement(self):
        source = make_docx(paragraph("出现灼热时应记录。"))
        plan = self.plan(
            source,
            before="灼热",
            after="烧灼感",
            rule="terminology_burning_sensation",
        )
        result = generate(source, plan)
        document = DocxPackage(result.content).xml(MAIN)
        self.assertEqual(visible_text(document), "出现烧灼感时应记录。")
        self.assertEqual(visible_text(document, original=True), "出现灼热时应记录。")
        self.assertEqual(len(result.revision_ids), 2)

    def test_replacement_enables_track_revisions_when_missing(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        parts = dict(DocxPackage(source).parts)
        parts["word/settings.xml"] = parts["word/settings.xml"].replace(
            b"<w:trackRevisions/>", b""
        )
        source = pack(parts)
        result = generate(source, self.plan(source))
        output = DocxPackage(result.content)
        self.assertIsNotNone(output.xml("word/settings.xml").find(w("trackRevisions")))
        for name, content in DocxPackage(source).parts.items():
            if name not in {MAIN, "word/settings.xml"}:
                self.assertEqual(output.parts[name], content, name)

    def test_comments_wire_all_parts_and_do_not_change_text(self):
        source = make_docx(paragraph("剂量需核对。"))
        result = generate(source, self.plan(source, before="剂量", after="", action="comment", rule="", comment="仅为合成测试：需专业人员确认。"))
        package = DocxPackage(result.content)
        doc = package.xml(MAIN)
        self.assertEqual(visible_text(doc), "剂量需核对。")
        self.assertEqual(len(result.comment_ids), 1)
        self.assertEqual(doc.xpath(".//w:commentRangeStart/@w:id", namespaces=NS), list(result.comment_ids))
        self.assertEqual(doc.xpath(".//w:commentRangeEnd/@w:id", namespaces=NS), list(result.comment_ids))
        self.assertEqual(doc.xpath(".//w:commentReference/@w:id", namespaces=NS), list(result.comment_ids))
        self.assertEqual(package.xml(COMMENTS)[0].get(w("author")), AUTHOR)
        self.assertIn(R + "/comments", package.parts[DOC_RELS].decode())
        self.assertIn("/word/comments.xml", package.parts["[Content_Types].xml"].decode())

    def test_append_comment_preserves_existing_comment(self):
        source = make_docx(paragraph("需要确认。"), history=True)
        result = generate(source, self.plan(source, before="确认", after="", action="comment", rule="", comment="新增合成批注。"))
        comments = DocxPackage(result.content).xml(COMMENTS)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0].get(w("author")), "原审阅者")
        self.assertEqual(visible_text(comments[0]), "不要删除此批注。")
        self.assertEqual(result.comment_ids, ("13",))

    def test_combined_replace_and_comment_preserve_existing_review_history(self):
        source = make_docx(
            paragraph("完成访视,记录结果。") + paragraph("给药剂量需确认。"), history=True
        )
        original = source[:]
        original_package = DocxPackage(source)
        original_document = original_package.xml(MAIN)
        original_old_review = {
            (node.tag, node.get(w("id"))): etree.tostring(node, method="c14n", exclusive=True)
            for node in original_document.iter()
            if node.tag in {w("ins"), w("del")}
        }
        original_old_comment = etree.tostring(
            original_package.xml(COMMENTS)[0], method="c14n", exclusive=True
        )

        replace_change = self.plan(source, index=0).changes[0]
        comment_change = replace(
            self.plan(
                source,
                index=1,
                before="给药剂量",
                after="",
                action="comment",
                rule="",
                comment="仅为合成测试：请医学负责人复核给药剂量。",
            ).changes[0],
            finding_id="F002",
        )
        plan = freeze_plan(digest(source), (replace_change, comment_change), WHEN)
        result = generate(source, plan)
        output = DocxPackage(result.content)
        document = output.xml(MAIN)
        comments = output.xml(COMMENTS)

        self.assertEqual(source, original)
        self.assertEqual(digest(source), result.source_sha256)
        self.assertEqual(result.revision_ids, ("13", "14"))
        self.assertEqual(result.comment_ids, ("15",))
        all_review_ids = [
            node.get(w("id"))
            for node in document.iter()
            if node.tag in {w("ins"), w("del"), w("commentRangeStart")}
        ]
        self.assertEqual(len(all_review_ids), len(set(all_review_ids)))
        for node in document.iter():
            key = (node.tag, node.get(w("id")))
            if key in original_old_review:
                self.assertEqual(
                    etree.tostring(node, method="c14n", exclusive=True), original_old_review[key]
                )
        self.assertEqual(
            etree.tostring(comments[0], method="c14n", exclusive=True), original_old_comment
        )
        for kind in ("commentRangeStart", "commentRangeEnd", "commentReference"):
            self.assertEqual(
                set(document.xpath(f".//w:{kind}/@w:id", namespaces=NS)), {"12", "15"}
            )
        self.assertEqual([node.get(w("id")) for node in comments], ["12", "15"])
        self.assertIn(R + "/comments", output.parts[DOC_RELS].decode())
        self.assertEqual(
            output.parts[DOC_RELS].decode().count(R + "/comments"), 1
        )
        self.assertEqual(
            output.parts["[Content_Types].xml"].decode().count("/word/comments.xml"), 1
        )
        self.assertEqual(
            output.parts["customXml/item1.xml"], original_package.parts["customXml/item1.xml"]
        )
        self.assertEqual(
            visible_text(document),
            "完成访视，记录结果。给药剂量需确认。待接受文字已有批注位置",
        )
        self.assertEqual(
            visible_text(document, original=True),
            "完成访视,记录结果。给药剂量需确认。旧文字已有批注位置",
        )

    def test_simple_table_cell_is_supported(self):
        body = '<w:tbl><w:tblGrid><w:gridCol w:w="9000"/></w:tblGrid><w:tr><w:tc>' + paragraph("完成访视,记录结果。") + '</w:tc></w:tr></w:tbl>'
        source = make_docx(body)
        result = generate(source, self.plan(source))
        self.assertIn("完成访视，记录结果。", visible_text(DocxPackage(result.content).xml(MAIN)))

    def test_changed_source_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        plan = self.plan(source)
        newer = make_docx(paragraph("完成访视,填写结果。"))
        self.assertReject("stale_source", generate, newer, plan)

    def test_changed_plan_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        plan = self.plan(source)
        changed = replace(plan.changes[0], after="0")
        self.assertReject("plan_hash_mismatch", generate, source, replace(plan, changes=(changed,)))

    def test_unconfirmed_change_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        self.assertReject("unconfirmed_change", self.plan, source, confirmed=False)

    def test_high_risk_number_change_rejected_despite_rule_label(self):
        source = make_docx(paragraph("剂量为10 mg。"))
        self.assertReject("change_not_in_mechanical_allowlist", generate, source,
                          self.plan(source, before="10", after="20", rule="duplicate_chinese_comma"))

    def test_numeric_comma_not_treated_as_chinese_sentence(self):
        source = make_docx(paragraph("数值为1,000。"))
        self.assertReject("change_not_in_mechanical_allowlist", generate, source, self.plan(source))

    def test_unknown_action_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        self.assertReject("unknown_action", generate, source, self.plan(source, action="accept_all"))

    def test_unsupported_hyperlink_anchor_rejected(self):
        source = make_docx('<w:p><w:hyperlink><w:r><w:t>完成访视,记录结果。</w:t></w:r></w:hyperlink></w:p>')
        self.assertReject("protected_paragraph_structure", make_anchor, source, 0, 4, 5)

    def test_edit_inside_existing_revision_rejected(self):
        source = make_docx(paragraph("正常正文。"), history=True)
        self.assertReject("protected_paragraph_structure", make_anchor, source, 1, 0, 1)

    def test_field_in_run_rejected(self):
        source = make_docx('<w:p><w:r><w:t>完成访视,记录结果。</w:t><w:fldChar w:fldCharType="begin"/><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        self.assertReject("complex_run_unsupported", make_anchor, source, 0, 4, 5)

    def test_merged_table_rejected(self):
        source = make_docx('<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>' + paragraph("完成访视,记录结果。") + '</w:tc></w:tr></w:tbl>')
        self.assertReject("complex_table_unsupported", make_anchor, source, 0, 4, 5)

    def test_cross_run_range_rejected(self):
        source = make_docx('<w:p><w:r><w:t>完成访视，</w:t></w:r><w:r><w:t>，记录结果。</w:t></w:r></w:p>')
        self.assertReject("cross_run_range_unsupported", generate, source,
                          self.plan(source, before="，，", after="，", rule="duplicate_chinese_comma"))

    def test_stale_paragraph_anchor_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        plan = self.plan(source)
        change = replace(plan.changes[0], anchor=replace(plan.changes[0].anchor, paragraph_sha256="0" * 64))
        self.assertReject("stale_paragraph", generate, source, freeze_plan(digest(source), (change,), WHEN))

    def test_wrong_quote_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        change = self.plan(source).changes[0]
        change = replace(change, anchor=replace(change.anchor, before="，"))
        self.assertReject("anchor_text_mismatch", generate, source, freeze_plan(digest(source), (change,), WHEN))

    def test_multiple_findings_same_paragraph_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        change = self.plan(source).changes[0]
        self.assertReject("duplicate_or_overlapping_change", generate, source,
                          freeze_plan(digest(source), (change, replace(change, finding_id="F002")), WHEN))

    def test_multiple_paragraph_changes(self):
        source = make_docx(paragraph("完成访视,记录结果。") + paragraph("记录结果,检查签名。"))
        first = self.plan(source).changes[0]
        second = replace(self.plan(source, index=1).changes[0], finding_id="F002")
        result = generate(source, freeze_plan(digest(source), (first, second), WHEN))
        self.assertEqual(len(result.revision_ids), 4)

    def test_reusing_plan_against_result_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        plan = self.plan(source)
        result = generate(source, plan)
        self.assertReject("stale_source", generate, result.content, plan)

    def test_modern_comments_fail_closed_not_stripped(self):
        source = make_docx(paragraph("完成访视,记录结果。"), extras={"word/commentsExtended.xml": b'<extended/>'})
        self.assertReject("modern_comments_not_yet_supported", generate, source, self.plan(source))

    def test_missing_comment_reference_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"), history=True)
        parts = dict(DocxPackage(source).parts)
        parts[MAIN] = parts[MAIN].replace(b'<w:commentReference w:id="12"/>', b'')
        broken = pack(parts)
        self.assertReject("incomplete_comment_anchors", generate, broken, self.plan(broken))

    def test_duplicate_revision_id_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"), history=True)
        parts = dict(DocxPackage(source).parts)
        parts[MAIN] = parts[MAIN].replace(b'w:id="8"', b'w:id="7"')
        broken = pack(parts)
        self.assertReject("duplicate_review_id", generate, broken, self.plan(broken))

    def test_wrong_comment_wiring_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"), history=True)
        parts = dict(DocxPackage(source).parts)
        parts[DOC_RELS] = parts[DOC_RELS].replace((R + "/comments").encode(), (R + "/other").encode())
        broken = pack(parts)
        self.assertReject("invalid_comment_wiring", generate, broken, self.plan(broken))

    def test_invalid_timestamp_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        changes = self.plan(source).changes
        self.assertReject("invalid_review_timestamp", freeze_plan, digest(source), changes, "2026-02-31T00:00:00Z")

    def test_empty_comment_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        self.assertReject("invalid_comment_action", generate, source,
                          self.plan(source, action="comment", after="", rule="", comment=" "))

    def test_field_result_in_middle_paragraph_is_rejected(self):
        body = ('<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText>TOC</w:instrText>'
                '<w:fldChar w:fldCharType="separate"/></w:r></w:p>' + paragraph("完成访视,记录结果。")
                + '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        source = make_docx(body)
        self.assertReject("anchor_inside_protected_range", make_anchor, source, 1, 4, 5)

    def test_unrelated_balanced_field_does_not_block_plain_paragraph(self):
        body = ('<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText>PAGE</w:instrText>'
                '<w:fldChar w:fldCharType="separate"/><w:t>1</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>'
                + paragraph("完成访视,记录结果。"))
        source = make_docx(body)
        self.assertEqual(len(generate(source, self.plan(source, index=1)).revision_ids), 2)

    def test_unclosed_field_anywhere_rejected(self):
        source = make_docx(paragraph("完成访视,记录结果。") + '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>')
        self.assertReject("unclosed_protected_range", make_anchor, source, 0, 4, 5)

    def test_cross_paragraph_bookmark_is_protected(self):
        source = make_docx('<w:p><w:bookmarkStart w:id="10" w:name="test"/></w:p>'
                           + paragraph("完成访视,记录结果。") + '<w:p><w:bookmarkEnd w:id="10"/></w:p>')
        self.assertReject("anchor_inside_protected_range", make_anchor, source, 1, 4, 5)

    def test_cell_review_variants_are_rejected(self):
        for tag in ("cellDel", "cellIns", "cellMerge"):
            with self.subTest(tag=tag):
                body = (f'<w:tbl><w:tr><w:tc><w:tcPr><w:{tag} w:id="19" w:author="Old" w:date="{WHEN}"/></w:tcPr>'
                        + paragraph("完成访视,记录结果。") + '</w:tc></w:tr></w:tbl>')
                source = make_docx(body)
                self.assertReject("complex_review_not_yet_supported", generate, source, self.plan(source))

    def test_renamed_modern_comment_part_is_rejected_by_type(self):
        source = make_docx(paragraph("完成访视,记录结果。"), extras={"word/reviewData.xml": b'<extended/>'})
        parts = dict(DocxPackage(source).parts)
        types = etree.fromstring(parts["[Content_Types].xml"])
        etree.SubElement(types, f"{{{CT}}}Override", PartName="/word/reviewData.xml",
                         ContentType="application/vnd.ms-word.commentsExtended+xml")
        parts["[Content_Types].xml"] = etree.tostring(types)
        source = pack(parts)
        self.assertReject("modern_comments_not_yet_supported", generate, source, self.plan(source))

    def test_renamed_modern_comment_part_is_rejected_by_relationship(self):
        source = make_docx(paragraph("完成访视,记录结果。"), extras={"word/reviewData.xml": b'<extended/>'},
                           rel_extra='<Relationship Id="rId2" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" Target="reviewData.xml"/>')
        self.assertReject("modern_comments_not_yet_supported", generate, source, self.plan(source))

    def test_root_sibling_processing_instructions_and_comments_are_preserved(self):
        source = make_docx(paragraph("完成访视,记录结果。"))
        parts = dict(DocxPackage(source).parts)
        parts[MAIN] = b'<?before keep?><!--before-comment-->' + parts[MAIN] + b'<!--after-comment--><?after keep?>'
        source = pack(parts)
        result = generate(source, self.plan(source))
        output = DocxPackage(result.content).parts[MAIN]
        for item in (b'<?before keep?>', b'<!--before-comment-->', b'<!--after-comment-->', b'<?after keep?>'):
            self.assertIn(item, output)

    def test_deleted_field_boundaries_cannot_hide_active_field(self):
        body = ('<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:fldChar w:fldCharType="separate"/></w:r></w:p>'
                f'<w:p><w:del w:id="10" w:author="Old" w:date="{WHEN}"><w:r><w:fldChar w:fldCharType="end"/></w:r></w:del></w:p>'
                + paragraph("完成访视,记录结果。")
                + f'<w:p><w:del w:id="11" w:author="Old" w:date="{WHEN}"><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:del></w:p>'
                '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        self.assertReject("reviewed_range_markers_unsupported", make_anchor, make_docx(body), 2, 4, 5)

    def test_old_insertion_with_range_markers_fails_closed(self):
        body = (paragraph("完成访视,记录结果。")
                + f'<w:p><w:ins w:id="10" w:author="Old" w:date="{WHEN}">'
                  '<w:bookmarkStart w:id="11" w:name="old"/><w:r><w:t>旧插入</w:t></w:r>'
                  '<w:bookmarkEnd w:id="11"/></w:ins></w:p>')
        self.assertReject("reviewed_range_markers_unsupported", make_anchor, make_docx(body), 0, 4, 5)


class ContainerTests(unittest.TestCase):
    def test_plain_text_is_not_docx(self):
        with self.assertRaisesRegex(RejectedDocument, "invalid_zip"):
            DocxPackage(b"plain text")

    def test_zip_path_escape(self):
        source = make_docx(paragraph("测试"), extras={"../escape.xml": b'<x/>'})
        with self.assertRaisesRegex(RejectedDocument, "unsafe_part_name"):
            DocxPackage(source)

    def test_duplicate_zip_entry(self):
        source = io.BytesIO(make_docx(paragraph("测试")))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(source, "a") as archive:
                archive.writestr(MAIN, b'<x/>')
        with self.assertRaisesRegex(RejectedDocument, "duplicate_part"):
            DocxPackage(source.getvalue())

    def test_case_alias_rejected(self):
        source = make_docx(paragraph("测试"), extras={"word/DOCUMENT.xml": b'<x/>'})
        with self.assertRaisesRegex(RejectedDocument, "duplicate_part"):
            DocxPackage(source)

    def test_macro_rejected(self):
        source = make_docx(paragraph("测试"), extras={"word/vbaProject.bin": b'fake macro'})
        with self.assertRaisesRegex(RejectedDocument, "active_or_signed_content"):
            DocxPackage(source)

    def test_signed_package_rejected(self):
        source = make_docx(paragraph("测试"), extras={"_xmlsignatures/sig1.xml": b'<x/>'})
        with self.assertRaisesRegex(RejectedDocument, "active_or_signed_content"):
            DocxPackage(source)

    def test_external_relationship_rejected(self):
        rel = f'<Relationship Id="rId2" Type="{R}/hyperlink" Target="https://example.invalid" TargetMode="External"/>'
        with self.assertRaisesRegex(RejectedDocument, "external_relationship_unsupported"):
            DocxPackage(make_docx(paragraph("测试"), rel_extra=rel))

    def test_entity_rejected_without_accessing_resource(self):
        xml = b'<!DOCTYPE root [<!ENTITY x SYSTEM "file:///must-not-be-read">]><root>&x;</root>'
        with self.assertRaisesRegex(RejectedDocument, "xml_dtd_or_entity"):
            DocxPackage(make_docx(paragraph("测试"), extras={"customXml/item2.xml": xml}))

    def test_oversized_part_rejected(self):
        with self.assertRaisesRegex(RejectedDocument, "unpacked_size_limit"):
            DocxPackage(make_docx(paragraph("测试")), Limits(part_bytes=30))

    def test_protected_document_rejected(self):
        xml = f'<w:settings xmlns:w="{W}"><w:documentProtection w:enforcement="1"/></w:settings>'.encode()
        with self.assertRaisesRegex(RejectedDocument, "protected_or_imported_content"):
            DocxPackage(make_docx(paragraph("测试"), extras={"word/settings.xml": xml}))

    def test_unresolved_relationship_rejected(self):
        rel = f'<Relationship Id="rId2" Type="{R}/image" Target="missing.png"/>'
        with self.assertRaisesRegex(RejectedDocument, "dangling_relationship"):
            DocxPackage(make_docx(paragraph("测试"), rel_extra=rel))

    def test_part_limit_rejected(self):
        with self.assertRaisesRegex(RejectedDocument, "part_count_limit"):
            DocxPackage(make_docx(paragraph("测试")), Limits(parts=2))

    def test_renamed_protected_settings_xml_is_rejected(self):
        xml = f'<w:settings xmlns:w="{W}"><w:documentProtection w:enforcement="1" w:edit="readOnly"/></w:settings>'.encode()
        source = make_docx(paragraph("测试"), extras={"word/policy.dat": xml},
                           rel_extra=f'<Relationship Id="rId3" Type="{R}/settings" Target="policy.dat"/>')
        # Construct valid typed OPC without first passing it through the guarded reader.
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        types = etree.fromstring(parts["[Content_Types].xml"])
        etree.SubElement(types, f"{{{CT}}}Override", PartName="/word/policy.dat",
                         ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml")
        parts["[Content_Types].xml"] = etree.tostring(types)
        with self.assertRaisesRegex(RejectedDocument, "protected_or_imported_content"):
            DocxPackage(pack(parts))

    def test_xml_relationship_mislabeled_as_binary_rejected(self):
        source = make_docx(paragraph("测试"), extras={"word/policy.bin": b'<settings/>'},
                           rel_extra=f'<Relationship Id="rId3" Type="{R}/settings" Target="policy.bin"/>')
        with self.assertRaisesRegex(RejectedDocument, "xml_relationship_content_type_mismatch"):
            DocxPackage(source)


if __name__ == "__main__":
    unittest.main()
