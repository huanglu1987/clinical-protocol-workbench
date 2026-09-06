"""G3 spike: conservative native review edits, not full OOXML/Word validation."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime

from lxml import etree

from .docx_package import (COMMENTS, COMMENT_TYPE, CT, DOC_RELS, MAIN, NS, R, REL,
                           DocxPackage, RejectedDocument, digest, serialize, w)

AUTHOR = "临床方案 QC（待人工复核）"
WRITER_VERSION = "0.1.0-spike"
SETTINGS = "word/settings.xml"


@dataclass(frozen=True)
class Anchor:
    source_sha256: str
    paragraph_index: int
    paragraph_sha256: str
    start: int
    end: int
    before: str


@dataclass(frozen=True)
class Change:
    finding_id: str
    anchor: Anchor
    action: str
    after: str = ""
    rule_id: str = ""
    comment: str = ""
    user_confirmed: bool = False


@dataclass(frozen=True)
class FrozenPlan:
    source_sha256: str
    changes: tuple[Change, ...]
    created_at: str
    sha256: str


@dataclass(frozen=True)
class Generated:
    content: bytes
    source_sha256: str
    output_sha256: str
    plan_sha256: str
    revision_ids: tuple[str, ...]
    comment_ids: tuple[str, ...]
    structural_status: str = "spike_subset_passed"
    word_visual_status: str = "not_performed"


def _plan_hash(source: str, changes: tuple[Change, ...], created: str) -> str:
    payload = {"source": source, "changes": [asdict(c) for c in changes],
               "created_at": created, "writer": WRITER_VERSION, "author": AUTHOR}
    return digest(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode())


def freeze_plan(source_sha256: str, changes: tuple[Change, ...], created_at: str) -> FrozenPlan:
    if not isinstance(changes, tuple) or not changes or len(changes) > 100:
        raise RejectedDocument("invalid_plan_size")
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise RejectedDocument("invalid_source_hash")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_at):
        raise RejectedDocument("invalid_review_timestamp")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise RejectedDocument("invalid_review_timestamp") from exc
    if any(c.user_confirmed is not True for c in changes):
        raise RejectedDocument("unconfirmed_change")
    return FrozenPlan(source_sha256, changes, created_at, _plan_hash(source_sha256, changes, created_at))


def visible_text(root: etree._Element, original: bool = False) -> str:
    result: list[str] = []
    excluded = w("ins") if original else w("del")
    for node in root.iter():
        if node.tag not in {w("t"), w("delText")} or any(p.tag == excluded for p in node.iterancestors()):
            continue
        result.append(node.text or "")
    return "".join(result)


def _paragraphs(root: etree._Element) -> list[etree._Element]:
    return list(root.iter(w("p")))


def _assert_outside_protected_ranges(document: etree._Element, paragraph: etree._Element) -> None:
    field_depth = 0
    ranges: set[tuple[str, str]] = set()
    starts = {w("bookmarkStart"): "bookmark", w("commentRangeStart"): "comment", w("permStart"): "permission"}
    ends = {w("bookmarkEnd"): "bookmark", w("commentRangeEnd"): "comment", w("permEnd"): "permission"}
    for node in document.iter():
        if node.tag in {*starts, *ends, w("fldChar")} and any(
            ancestor.tag in {w("ins"), w("del")} for ancestor in node.iterancestors()
        ):
            # Physical XML order is not the current semantic view inside old revisions.
            raise RejectedDocument("reviewed_range_markers_unsupported")
        if node is paragraph and (field_depth or ranges):
            raise RejectedDocument("anchor_inside_protected_range")
        if node.tag == w("fldChar"):
            kind = node.get(w("fldCharType"))
            if kind == "begin":
                field_depth += 1
            elif kind == "end" and field_depth:
                field_depth -= 1
            elif kind != "separate" or not field_depth:
                raise RejectedDocument("malformed_field_range")
        if node.tag in starts:
            key = (starts[node.tag], node.get(w("id"), ""))
            if not key[1] or key in ranges:
                raise RejectedDocument("malformed_protected_range")
            ranges.add(key)
        elif node.tag in ends:
            key = (ends[node.tag], node.get(w("id"), ""))
            if key not in ranges:
                raise RejectedDocument("malformed_protected_range")
            ranges.remove(key)
    if field_depth or ranges:
        raise RejectedDocument("unclosed_protected_range")


def _safe_paragraph(paragraph: etree._Element) -> list[etree._Element]:
    parent = paragraph.getparent()
    if parent is None or parent.tag not in {w("body"), w("tc")}:
        raise RejectedDocument("unsupported_paragraph_location")
    if parent.tag == w("tc"):
        chain = [n.tag for n in paragraph.iterancestors()]
        if chain != [w("tc"), w("tr"), w("tbl"), w("body"), w("document")]:
            raise RejectedDocument("complex_table_unsupported")
        table = parent.getparent().getparent()
        if table.xpath(".//w:vMerge | .//w:hMerge | .//w:gridSpan | .//w:tblPrChange | .//w:trPrChange | .//w:tcPrChange", namespaces=NS):
            raise RejectedDocument("complex_table_unsupported")
    runs: list[etree._Element] = []
    for child in paragraph:
        if child.tag == w("pPr"):
            if any(isinstance(n.tag, str) and n.tag.endswith("Change") for n in child.iter()):
                raise RejectedDocument("reviewed_properties_unsupported")
            continue
        if child.tag != w("r"):
            raise RejectedDocument("protected_paragraph_structure")
        if len(child.findall(w("t"))) != 1 or any(n.tag not in {w("rPr"), w("t")} for n in child):
            raise RejectedDocument("complex_run_unsupported")
        if any(n.tag == w("rPrChange") for n in child.iter()):
            raise RejectedDocument("reviewed_properties_unsupported")
        if len(child.find(w("t"))):
            raise RejectedDocument("complex_text_unsupported")
        runs.append(child)
    if not runs:
        raise RejectedDocument("empty_paragraph")
    return runs


def make_anchor(source: bytes, paragraph_index: int, start: int, end: int) -> Anchor:
    package = DocxPackage(source)
    document = package.xml(MAIN)
    paragraphs = _paragraphs(document)
    if type(paragraph_index) is not int or not 0 <= paragraph_index < len(paragraphs):
        raise RejectedDocument("invalid_paragraph_index")
    paragraph = paragraphs[paragraph_index]
    _assert_outside_protected_ranges(document, paragraph)
    _safe_paragraph(paragraph)
    text = visible_text(paragraph)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise RejectedDocument("invalid_character_range")
    return Anchor(package.source_hash, paragraph_index, digest(etree.tostring(paragraph)), start, end, text[start:end])


def _numeric_ids(root: etree._Element, tags: set[str], unique: bool) -> list[str]:
    ids: list[str] = []
    for node in root.iter():
        if node.tag not in tags:
            continue
        value = node.get(w("id"), "")
        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) >= 2**31 - 200:
            raise RejectedDocument("invalid_review_id")
        if unique and value in ids:
            raise RejectedDocument("duplicate_review_id")
        ids.append(value)
    return ids


def _review_inventory(package: DocxPackage) -> dict[str, bytes]:
    inventory: dict[str, bytes] = {}
    revision_tags = {w("ins"), w("del")}
    for item in package.xml("[Content_Types].xml"):
        content_type = item.get("ContentType", "")
        if "comment" in content_type.lower() and content_type != COMMENT_TYPE:
            raise RejectedDocument("modern_comments_not_yet_supported")
    for name in package.parts:
        if name.endswith(".rels"):
            for item in package.xml(name):
                relation_type = item.get("Type", "")
                if "comment" in relation_type.lower() and relation_type != R + "/comments":
                    raise RejectedDocument("modern_comments_not_yet_supported")
    for name in package.xml_parts:
        if name == "[Content_Types].xml" or name.endswith(".rels"):
            continue
        root = package.xml(name)
        # Extended review formats remain outside this first spike, never stripped.
        if "comment" in name.lower() and name != COMMENTS:
            raise RejectedDocument("modern_comments_not_yet_supported")
        for node in root.iter():
            if isinstance(node.tag, str) and (node.tag.endswith("Change") or node.tag in {
                w("cellDel"), w("cellIns"), w("cellMerge")} or node.tag.startswith(w("move"))
                    or (node.tag.startswith(w("customXml")) and "Range" in node.tag)):
                raise RejectedDocument("complex_review_not_yet_supported")
            if node.tag in {w("fldChar"), w("bookmarkStart"), w("bookmarkEnd"),
                            w("commentRangeStart"), w("commentRangeEnd"), w("permStart"), w("permEnd")}:
                if any(ancestor.tag in revision_tags for ancestor in node.iterancestors()):
                    raise RejectedDocument("reviewed_range_markers_unsupported")
        _numeric_ids(root, revision_tags, unique=True)
        for node in root.iter():
            if node.tag not in revision_tags:
                continue
            if node.getparent().tag != w("p") or any(n.tag in revision_tags for n in list(node.iter())[1:]):
                raise RejectedDocument("nested_review_unsupported")
            if not node.get(w("author")) or not node.get(w("date")):
                raise RejectedDocument("incomplete_review_metadata")
            if node.tag == w("del") and any(n.tag == w("t") for n in node.iter()):
                raise RejectedDocument("invalid_deleted_text")
            inventory[f"{name}:{node.tag}:{node.get(w('id'))}"] = etree.tostring(node, method="c14n", exclusive=True)
    document = package.xml(MAIN)
    starts = _numeric_ids(document, {w("commentRangeStart")}, unique=True)
    ends = _numeric_ids(document, {w("commentRangeEnd")}, unique=True)
    references = _numeric_ids(document, {w("commentReference")}, unique=True)
    comment_root = package.xml(COMMENTS) if COMMENTS in package.parts else etree.Element(w("comments"))
    if comment_root.tag != w("comments"):
        raise RejectedDocument("invalid_comments_root")
    bodies = _numeric_ids(comment_root, {w("comment")}, unique=True)
    if set(starts) != set(ends) or set(starts) != set(references) or set(starts) != set(bodies):
        raise RejectedDocument("incomplete_comment_anchors")
    order = list(document.iter())
    for cid in starts:
        begin = next(n for n in order if n.tag == w("commentRangeStart") and n.get(w("id")) == cid)
        end = next(n for n in order if n.tag == w("commentRangeEnd") and n.get(w("id")) == cid)
        ref = next(n for n in order if n.tag == w("commentReference") and n.get(w("id")) == cid)
        if not order.index(begin) < order.index(end) < order.index(ref):
            raise RejectedDocument("invalid_comment_order")
        if begin.getparent().tag != w("p") or end.getparent().tag != w("p") or ref.getparent().tag != w("r"):
            raise RejectedDocument("invalid_comment_nesting")
    for node in comment_root:
        if node.tag != w("comment") or not node.get(w("author")) or not node.get(w("date")):
            raise RejectedDocument("incomplete_comment_metadata")
        inventory[f"{COMMENTS}:{node.get(w('id'))}"] = etree.tostring(node, method="c14n", exclusive=True)
    links = package.xml(DOC_RELS) if DOC_RELS in package.parts else etree.Element(f"{{{REL}}}Relationships")
    comment_links = [n for n in links if n.get("Type") == R + "/comments"]
    types = package.xml("[Content_Types].xml")
    overrides = [n for n in types if n.get("PartName") == "/" + COMMENTS and n.get("ContentType") == COMMENT_TYPE]
    if COMMENTS in package.parts:
        if len(comment_links) != 1 or comment_links[0].get("Target") != "comments.xml" or len(overrides) != 1:
            raise RejectedDocument("invalid_comment_wiring")
    elif comment_links or overrides:
        raise RejectedDocument("invalid_comment_wiring")
    return inventory


def _check_rule(change: Change, paragraph_text: str) -> None:
    before, after = change.anchor.before, change.after
    if change.rule_id == "duplicate_chinese_comma" and before == "，，" and after == "，":
        return
    if change.rule_id == "chinese_sentence_comma" and before == "," and after == "，":
        a = change.anchor
        if (a.start > 0 and a.end < len(paragraph_text)
                and all("\u4e00" <= c <= "\u9fff" for c in (paragraph_text[a.start - 1], paragraph_text[a.end]))):
            return
    if (change.rule_id == "terminology_burning_sensation"
            and before == "灼热" and after == "烧灼感"):
        return
    raise RejectedDocument("change_not_in_mechanical_allowlist")


def _new_run(template: etree._Element, text: str, deleted: bool = False) -> etree._Element:
    result = copy.deepcopy(template)
    for child in list(result):
        if child.tag != w("rPr"):
            result.remove(child)
    text_node = etree.SubElement(result, w("delText") if deleted else w("t"))
    text_node.text = text
    text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return result


def _minimal(before: str, after: str) -> tuple[int, str, str]:
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    before, after = before[prefix:], after[prefix:]
    suffix = 0
    while suffix < min(len(before), len(after)) and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    return prefix, before[:len(before) - suffix], after[:len(after) - suffix]


def generate(source: bytes, plan: FrozenPlan) -> Generated:
    package = DocxPackage(source)
    if plan.source_sha256 != package.source_hash:
        raise RejectedDocument("stale_source")
    if plan.sha256 != _plan_hash(plan.source_sha256, plan.changes, plan.created_at):
        raise RejectedDocument("plan_hash_mismatch")
    freeze_plan(plan.source_sha256, plan.changes, plan.created_at)
    original_inventory = _review_inventory(package)
    document = package.xml(MAIN)
    source_current, source_original = visible_text(document), visible_text(document, original=True)
    paragraphs = _paragraphs(document)
    used_paragraphs: set[int] = set()
    finding_ids: set[str] = set()
    resolved = []
    for change in plan.changes:
        anchor = change.anchor
        if (not change.finding_id or change.finding_id in finding_ids
                or anchor.paragraph_index in used_paragraphs):
            raise RejectedDocument("duplicate_or_overlapping_change")
        finding_ids.add(change.finding_id)
        used_paragraphs.add(anchor.paragraph_index)
        if anchor.source_sha256 != package.source_hash:
            raise RejectedDocument("stale_anchor")
        if type(anchor.paragraph_index) is not int or not 0 <= anchor.paragraph_index < len(paragraphs):
            raise RejectedDocument("invalid_paragraph_index")
        paragraph = paragraphs[anchor.paragraph_index]
        if digest(etree.tostring(paragraph)) != anchor.paragraph_sha256:
            raise RejectedDocument("stale_paragraph")
        _assert_outside_protected_ranges(document, paragraph)
        runs = _safe_paragraph(paragraph)
        text = visible_text(paragraph)
        if (type(anchor.start) is not int or type(anchor.end) is not int
                or not 0 <= anchor.start < anchor.end <= len(text)
                or text[anchor.start:anchor.end] != anchor.before):
            raise RejectedDocument("anchor_text_mismatch")
        if change.action == "replace":
            if change.comment:
                raise RejectedDocument("mixed_action_unsupported")
            _check_rule(change, text)
        elif change.action == "comment":
            if change.after or change.rule_id or not change.comment.strip() or len(change.comment) > 4000:
                raise RejectedDocument("invalid_comment_action")
            try:
                etree.Element(w("t")).text = change.comment
            except ValueError as exc:
                raise RejectedDocument("invalid_comment_text") from exc
        else:
            raise RejectedDocument("unknown_action")
        offset = 0
        for run in runs:
            run_text = run.find(w("t")).text or ""
            if offset <= anchor.start < anchor.end <= offset + len(run_text):
                resolved.append((change, paragraph, run, anchor.start - offset, anchor.end - offset))
                break
            offset += len(run_text)
        else:
            raise RejectedDocument("cross_run_range_unsupported")
    all_ids = [int(n.get(w("id"))) for name in package.xml_parts
               for n in package.xml(name).iter() if n.get(w("id"), "").isdigit()]
    next_id = max(all_ids, default=0) + 1
    if next_id + 3 * len(resolved) >= 2**31:
        raise RejectedDocument("review_id_exhausted")
    comments = package.xml(COMMENTS) if COMMENTS in package.parts else etree.Element(w("comments"), nsmap={"w": NS["w"]})
    revision_ids: list[str] = []
    comment_ids: list[str] = []
    for change, paragraph, run, start, end in resolved:
        text = run.find(w("t")).text or ""
        nodes: list[etree._Element] = []
        if change.action == "replace":
            prefix, old, new = _minimal(change.anchor.before, change.after)
            start += prefix
            end = start + len(old)
            if text[:start]:
                nodes.append(_new_run(run, text[:start]))
            for value, tag in ((old, "del"), (new, "ins")):
                if not value:
                    continue
                cid = str(next_id)
                next_id += 1
                node = etree.Element(w(tag), {w("id"): cid, w("author"): AUTHOR, w("date"): plan.created_at})
                node.append(_new_run(run, value, deleted=tag == "del"))
                nodes.append(node)
                revision_ids.append(cid)
        else:
            if text[:start]:
                nodes.append(_new_run(run, text[:start]))
            cid = str(next_id)
            next_id += 1
            nodes.extend([etree.Element(w("commentRangeStart"), {w("id"): cid}),
                          _new_run(run, text[start:end]),
                          etree.Element(w("commentRangeEnd"), {w("id"): cid})])
            reference_run = etree.Element(w("r"))
            etree.SubElement(reference_run, w("commentReference"), {w("id"): cid})
            nodes.append(reference_run)
            comment = etree.SubElement(comments, w("comment"), {w("id"): cid, w("author"): AUTHOR, w("date"): plan.created_at})
            comment_p = etree.SubElement(comment, w("p"))
            comment_run = etree.SubElement(comment_p, w("r"))
            comment_text = etree.SubElement(comment_run, w("t"))
            comment_text.text = change.comment
            comment_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            comment_ids.append(cid)
        if text[end:]:
            nodes.append(_new_run(run, text[end:]))
        position = paragraph.index(run)
        paragraph.remove(run)
        for index, node in enumerate(nodes):
            paragraph.insert(position + index, node)
    replacements = {MAIN: serialize(document)}
    if revision_ids:
        if SETTINGS not in package.parts:
            raise RejectedDocument("missing_settings_for_tracked_revisions")
        settings = package.xml(SETTINGS)
        if settings.find(w("trackRevisions")) is None:
            settings.insert(0, etree.Element(w("trackRevisions")))
            replacements[SETTINGS] = serialize(settings)
    if comment_ids:
        replacements[COMMENTS] = serialize(comments)
        if COMMENTS not in package.parts:
            links = package.xml(DOC_RELS) if DOC_RELS in package.parts else etree.Element(f"{{{REL}}}Relationships", nsmap={None: REL})
            used = {n.get("Id") for n in links}
            rid = 1
            while f"rId{rid}" in used:
                rid += 1
            etree.SubElement(links, f"{{{REL}}}Relationship", Id=f"rId{rid}", Type=R + "/comments", Target="comments.xml")
            replacements[DOC_RELS] = serialize(links)
            types = package.xml("[Content_Types].xml")
            etree.SubElement(types, f"{{{CT}}}Override", PartName="/" + COMMENTS, ContentType=COMMENT_TYPE)
            replacements["[Content_Types].xml"] = serialize(types)
    output = package.updated(replacements)
    validated = DocxPackage(output)
    output_inventory = _review_inventory(validated)
    if any(output_inventory.get(key) != value for key, value in original_inventory.items()):
        raise RejectedDocument("existing_review_changed")
    for name, data in package.parts.items():
        if name not in replacements and validated.parts.get(name) != data:
            raise RejectedDocument("unplanned_part_change")
    restored = validated.xml(MAIN)
    for node in list(restored.iter()):
        if node.get(w("id")) not in revision_ids or node.tag not in {w("ins"), w("del")}:
            continue
        parent = node.getparent()
        position = parent.index(node)
        if node.tag == w("del"):
            for index, child in enumerate(list(node)):
                for t in child.iter(w("delText")):
                    t.tag = w("t")
                parent.insert(position + index, child)
        parent.remove(node)
    if visible_text(restored) != source_current or visible_text(restored, original=True) != source_original:
        raise RejectedDocument("semantic_restore_failed")
    return Generated(output, package.source_hash, digest(output), plan.sha256,
                     tuple(revision_ids), tuple(comment_ids))
