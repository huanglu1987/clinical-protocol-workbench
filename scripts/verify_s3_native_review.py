"""Persist an evidence-focused structural check for the S3 synthetic DOCX pair."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

from clinical_qc.docx_package import (  # noqa: E402
    COMMENTS,
    COMMENT_TYPE,
    DOC_RELS,
    MAIN,
    NS,
    R,
    DocxPackage,
    digest,
    w,
)
from clinical_qc.redline import (  # noqa: E402
    AUTHOR,
    WRITER_VERSION,
    Anchor,
    Change,
    FrozenPlan,
    visible_text,
)


SETTINGS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
EXPECTED_SOURCE_SHA256 = "0385d26b1b9808aa2f3071caeeb771ad2225b777df8063cb93c9fc31f8640d10"
EXPECTED_OUTPUT_SHA256 = "63df0e3a1240645aadde9e39b0c2f97d1f3c69ff91fe8ea6ac9e7cb7c08995e8"
EXPECTED_PLAN_SHA256 = "5277676d379ca8ddc7469685099b53058353e973e90bca389921a45b52711462"


def _load_plan(path: Path) -> FrozenPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    changes = tuple(
        Change(anchor=Anchor(**item.pop("anchor")), **item)
        for original in payload["changes"]
        for item in [dict(original)]
    )
    return FrozenPlan(
        source_sha256=payload["source_sha256"],
        changes=changes,
        created_at=payload["created_at"],
        sha256=payload["sha256"],
    )


def _canonical(node: etree._Element) -> bytes:
    return etree.tostring(node, method="c14n", exclusive=True)


def _recomputed_plan_hash(plan: FrozenPlan) -> str:
    payload = {
        "source": plan.source_sha256,
        "changes": [asdict(change) for change in plan.changes],
        "created_at": plan.created_at,
        "writer": WRITER_VERSION,
        "author": AUTHOR,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return digest(serialized)


def _plan_is_well_formed(plan: FrozenPlan) -> bool:
    try:
        datetime.strptime(plan.created_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    finding_ids = [change.finding_id for change in plan.changes]
    return (
        0 < len(plan.changes) <= 100
        and re.fullmatch(r"[0-9a-f]{64}", plan.source_sha256) is not None
        and len(finding_ids) == len(set(finding_ids))
        and all(change.action in {"replace", "comment"} for change in plan.changes)
        and all(change.user_confirmed is True for change in plan.changes)
        and all(change.anchor.source_sha256 == plan.source_sha256 for change in plan.changes)
        and plan.sha256 == _recomputed_plan_hash(plan)
    )


def _anchor_matches_source(
    change: Change,
    source: DocxPackage,
    source_paragraphs: list[etree._Element],
) -> bool:
    anchor = change.anchor
    if (
        type(anchor.paragraph_index) is not int
        or not 0 <= anchor.paragraph_index < len(source_paragraphs)
        or type(anchor.start) is not int
        or type(anchor.end) is not int
    ):
        return False
    paragraph = source_paragraphs[anchor.paragraph_index]
    text = visible_text(paragraph)
    return (
        anchor.source_sha256 == source.source_hash
        and anchor.paragraph_sha256 == digest(etree.tostring(paragraph))
        and 0 <= anchor.start < anchor.end <= len(text)
        and text[anchor.start : anchor.end] == anchor.before
    )


def _paragraph_ancestor(node: etree._Element) -> etree._Element | None:
    current = node
    while current is not None and current.tag != w("p"):
        current = current.getparent()
    return current


def _paragraph_child(paragraph: etree._Element, node: etree._Element) -> etree._Element | None:
    current = node
    while current is not None and current.getparent() is not paragraph:
        current = current.getparent()
    return current


def _replacement_changes_match_output(
    plan: FrozenPlan,
    source_paragraphs: list[etree._Element],
    output_paragraphs: list[etree._Element],
    new_revision_ids: set[str],
) -> bool:
    consumed: set[str] = set()
    for change in (item for item in plan.changes if item.action == "replace"):
        source_paragraph = source_paragraphs[change.anchor.paragraph_index]
        output_paragraph = output_paragraphs[change.anchor.paragraph_index]
        nodes = [
            node
            for node in output_paragraph.iter()
            if node.tag in {w("ins"), w("del")} and node.get(w("id")) in new_revision_ids
        ]
        deleted = [node for node in nodes if node.tag == w("del")]
        inserted = [node for node in nodes if node.tag == w("ins")]
        if len(nodes) != 2 or len(deleted) != 1 or len(inserted) != 1:
            return False
        deleted_node, inserted_node = deleted[0], inserted[0]
        if (
            deleted_node.getparent() is not output_paragraph
            or inserted_node.getparent() is not output_paragraph
        ):
            return False
        children = list(output_paragraph)
        deleted_index = children.index(deleted_node)
        inserted_index = children.index(inserted_node)
        if inserted_index != deleted_index + 1:
            return False
        source_text = visible_text(source_paragraph)
        expected_current = (
            source_text[: change.anchor.start]
            + change.after
            + source_text[change.anchor.end :]
        )
        prefix = "".join(visible_text(child) for child in children[:deleted_index])
        if (
            prefix != source_text[: change.anchor.start]
            or "".join(deleted_node.xpath(".//w:delText/text()", namespaces=NS))
            != change.anchor.before
            or "".join(inserted_node.xpath(".//w:t/text()", namespaces=NS)) != change.after
            or visible_text(output_paragraph) != expected_current
            or visible_text(output_paragraph, original=True) != source_text
        ):
            return False
        consumed.update(node.get(w("id")) for node in nodes)
    return consumed == new_revision_ids


def _comment_changes_match_output(
    plan: FrozenPlan,
    source_paragraphs: list[etree._Element],
    output_paragraphs: list[etree._Element],
    output_comments: etree._Element,
    new_comment_ids: list[str],
) -> bool:
    changes = [item for item in plan.changes if item.action == "comment"]
    if len(changes) != len(new_comment_ids):
        return False
    body_by_id = {node.get(w("id")): node for node in output_comments}
    for change, comment_id in zip(changes, new_comment_ids):
        source_paragraph = source_paragraphs[change.anchor.paragraph_index]
        output_paragraph = output_paragraphs[change.anchor.paragraph_index]
        starts = output_paragraph.xpath(
            ".//w:commentRangeStart[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        ends = output_paragraph.xpath(
            ".//w:commentRangeEnd[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        references = output_paragraph.xpath(
            ".//w:commentReference[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        body = body_by_id.get(comment_id)
        if len(starts) != 1 or len(ends) != 1 or len(references) != 1 or body is None:
            return False
        start_child = _paragraph_child(output_paragraph, starts[0])
        end_child = _paragraph_child(output_paragraph, ends[0])
        reference_child = _paragraph_child(output_paragraph, references[0])
        if start_child is None or end_child is None or reference_child is None:
            return False
        children = list(output_paragraph)
        start_index = children.index(start_child)
        end_index = children.index(end_child)
        reference_index = children.index(reference_child)
        source_text = visible_text(source_paragraph)
        prefix = "".join(visible_text(child) for child in children[:start_index])
        selected = "".join(
            visible_text(child) for child in children[start_index + 1 : end_index]
        )
        if (
            not start_index < end_index < reference_index
            or prefix != source_text[: change.anchor.start]
            or selected != change.anchor.before
            or visible_text(output_paragraph) != source_text
            or visible_text(output_paragraph, original=True) != source_text
            or visible_text(body) != change.comment
            or not change.comment.strip()
        ):
            return False
    return True


def _review_nodes(document: etree._Element) -> dict[tuple[str, str], bytes]:
    return {
        (etree.QName(node).localname, node.get(w("id"))): _canonical(node)
        for node in document.iter()
        if node.tag in {w("ins"), w("del")}
    }


def _comment_anchors(document: etree._Element, ids: set[str]) -> dict[tuple[str, str], bytes]:
    tags = {w("commentRangeStart"), w("commentRangeEnd"), w("commentReference")}
    return {
        (etree.QName(node).localname, node.get(w("id"))): _canonical(node)
        for node in document.iter()
        if node.tag in tags and node.get(w("id")) in ids
    }


def _restore_new_revisions(document: etree._Element, ids: set[str]) -> etree._Element:
    restored = deepcopy(document)
    for node in list(restored.iter()):
        if node.tag not in {w("ins"), w("del")} or node.get(w("id")) not in ids:
            continue
        parent = node.getparent()
        position = parent.index(node)
        if node.tag == w("del"):
            for index, child in enumerate(list(node)):
                for text in child.iter(w("delText")):
                    text.tag = w("t")
                parent.insert(position + index, child)
        parent.remove(node)
    return restored


def verify(source_bytes: bytes, output_bytes: bytes, plan: FrozenPlan) -> dict[str, object]:
    source = DocxPackage(source_bytes)
    output = DocxPackage(output_bytes)
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    checks["frozen_plan_hash_and_confirmations_valid"] = _plan_is_well_formed(plan)
    checks["source_hash_matches_frozen_plan"] = source.source_hash == plan.source_sha256
    checks["frozen_fixture_hashes_match_baseline"] = (
        source.source_hash == EXPECTED_SOURCE_SHA256
        and digest(output_bytes) == EXPECTED_OUTPUT_SHA256
        and plan.sha256 == EXPECTED_PLAN_SHA256
    )
    source_document = source.xml(MAIN)
    output_document = output.xml(MAIN)
    source_paragraphs = list(source_document.iter(w("p")))
    output_paragraphs = list(output_document.iter(w("p")))
    anchors_match_source = all(
        _anchor_matches_source(change, source, source_paragraphs) for change in plan.changes
    )
    checks["all_change_anchors_match_source"] = anchors_match_source
    source_reviews = _review_nodes(source_document)
    output_reviews = _review_nodes(output_document)
    old_revision_ids = {key[1] for key in source_reviews}
    new_revision_ids = sorted(
        {key[1] for key in output_reviews} - old_revision_ids, key=int
    )

    source_comments = source.xml(COMMENTS)
    output_comments = output.xml(COMMENTS)
    old_comment_ids = {node.get(w("id")) for node in source_comments}
    new_comment_ids = sorted(
        {node.get(w("id")) for node in output_comments} - old_comment_ids, key=int
    )
    checks["existing_revision_nodes_preserved"] = all(
        output_reviews.get(key) == value for key, value in source_reviews.items()
    )
    output_comment_by_id = {node.get(w("id")): node for node in output_comments}
    checks["existing_comment_bodies_preserved"] = all(
        node.get(w("id")) in output_comment_by_id
        and _canonical(output_comment_by_id[node.get(w("id"))]) == _canonical(node)
        for node in source_comments
    )
    checks["existing_comment_anchors_preserved"] = _comment_anchors(
        source_document, old_comment_ids
    ) == _comment_anchors(output_document, old_comment_ids)
    old_review_nodes = [
        node for node in source_document.iter() if node.tag in {w("ins"), w("del")}
    ]
    checks["source_fixture_history_expected"] = (
        sorted(old_revision_ids, key=int) == ["7", "8"]
        and sorted(old_comment_ids, key=int) == ["12"]
        and all(
            node.get(w("author")) == "原审阅者"
            and node.get(w("date")) == "2026-08-01T00:00:00Z"
            for node in old_review_nodes + list(source_comments)
        )
    )

    revision_nodes = [
        node
        for node in output_document.iter()
        if node.tag in {w("ins"), w("del")}
    ]
    comment_bodies = list(output_comments)
    entity_ids = [node.get(w("id")) for node in revision_nodes + comment_bodies]
    checks["review_entity_ids_unique"] = len(entity_ids) == len(set(entity_ids))
    checks["new_review_metadata_matches_plan"] = all(
        node.get(w("author")) == AUTHOR and node.get(w("date")) == plan.created_at
        for node in revision_nodes + comment_bodies
        if node.get(w("id")) in set(new_revision_ids + new_comment_ids)
    )

    starts = output_document.xpath(".//w:commentRangeStart/@w:id", namespaces=NS)
    ends = output_document.xpath(".//w:commentRangeEnd/@w:id", namespaces=NS)
    references = output_document.xpath(".//w:commentReference/@w:id", namespaces=NS)
    body_ids = [node.get(w("id")) for node in comment_bodies]
    document_order = list(output_document.iter())
    anchor_structure_valid = True
    for comment_id in set(starts):
        start_nodes = output_document.xpath(
            ".//w:commentRangeStart[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        end_nodes = output_document.xpath(
            ".//w:commentRangeEnd[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        reference_nodes = output_document.xpath(
            ".//w:commentReference[@w:id=$cid]", namespaces=NS, cid=comment_id
        )
        if len(start_nodes) != 1 or len(end_nodes) != 1 or len(reference_nodes) != 1:
            anchor_structure_valid = False
            continue
        start_node, end_node, reference_node = (
            start_nodes[0],
            end_nodes[0],
            reference_nodes[0],
        )
        paragraph = _paragraph_ancestor(start_node)
        if (
            paragraph is None
            or _paragraph_ancestor(end_node) is not paragraph
            or _paragraph_ancestor(reference_node) is not paragraph
            or reference_node.getparent() is None
            or reference_node.getparent().tag != w("r")
            or not (
                document_order.index(start_node)
                < document_order.index(end_node)
                < document_order.index(reference_node)
            )
        ):
            anchor_structure_valid = False
    checks["comment_anchors_are_one_to_one"] = (
        len(starts) == len(set(starts))
        and len(ends) == len(set(ends))
        and len(references) == len(set(references))
        and len(body_ids) == len(set(body_ids))
        and set(starts) == set(ends) == set(references) == set(body_ids)
        and anchor_structure_valid
    )
    relationships = output.xml(DOC_RELS)
    comment_links = [item for item in relationships if item.get("Type") == R + "/comments"]
    overrides = output.xml("[Content_Types].xml").xpath(
        "./ct:Override[@PartName='/word/comments.xml']",
        namespaces={"ct": "http://schemas.openxmlformats.org/package/2006/content-types"},
    )
    checks["classic_comment_wiring_valid"] = (
        len(comment_links) == 1
        and comment_links[0].get("Target") == "comments.xml"
        and len(overrides) == 1
        and overrides[0].get("ContentType") == COMMENT_TYPE
    )
    settings_links = [item for item in relationships if item.get("Type") == R + "/settings"]
    settings_overrides = output.xml("[Content_Types].xml").xpath(
        "./ct:Override[@PartName='/word/settings.xml']",
        namespaces={"ct": "http://schemas.openxmlformats.org/package/2006/content-types"},
    )
    checks["settings_wiring_and_track_revisions_valid"] = (
        len(settings_links) == 1
        and settings_links[0].get("Target") == "settings.xml"
        and len(settings_overrides) == 1
        and settings_overrides[0].get("ContentType") == SETTINGS_TYPE
        and output.xml("word/settings.xml").find(w("trackRevisions")) is not None
    )

    expected_revision_actions = sum(1 for change in plan.changes if change.action == "replace")
    expected_comments = sum(1 for change in plan.changes if change.action == "comment")
    checks["plan_maps_to_new_review_entities"] = (
        len(new_revision_ids) == 2 * expected_revision_actions
        and len(new_comment_ids) == expected_comments
    )
    checks["replacement_ooxml_matches_plan"] = (
        anchors_match_source
        and _replacement_changes_match_output(
            plan,
            source_paragraphs,
            output_paragraphs,
            set(new_revision_ids),
        )
    )
    checks["new_comment_content_and_anchor_match_plan"] = (
        anchors_match_source
        and _comment_changes_match_output(
            plan,
            source_paragraphs,
            output_paragraphs,
            output_comments,
            new_comment_ids,
        )
    )

    restored = _restore_new_revisions(output_document, set(new_revision_ids))
    checks["reject_new_revisions_restores_source_semantics"] = (
        visible_text(restored) == visible_text(source_document)
        and visible_text(restored, original=True) == visible_text(source_document, original=True)
    )
    allowed_changed_parts = {MAIN, COMMENTS}
    if COMMENTS not in source.parts:
        allowed_changed_parts |= {DOC_RELS, "[Content_Types].xml"}
    changed_parts = sorted(
        name for name, content in source.parts.items() if output.parts.get(name) != content
    )
    checks["unplanned_package_parts_byte_preserved"] = (
        not (set(changed_parts) - allowed_changed_parts)
        and set(output.parts) - set(source.parts) <= allowed_changed_parts
    )
    with zipfile.ZipFile(io.BytesIO(output_bytes)) as archive:
        checks["zip_crc_valid"] = archive.testzip() is None

    details.update(
        {
            "source_sha256": source.source_hash,
            "output_sha256": digest(output_bytes),
            "plan_sha256": plan.sha256,
            "source_old_revision_ids": sorted(old_revision_ids, key=int),
            "source_old_comment_ids": sorted(old_comment_ids, key=int),
            "output_new_revision_ids": new_revision_ids,
            "output_new_comment_ids": new_comment_ids,
            "changed_package_parts": changed_parts,
            "current_view_before": visible_text(source_document),
            "current_view_after": visible_text(output_document),
            "original_view_before": visible_text(source_document, original=True),
            "original_view_after": visible_text(output_document, original=True),
        }
    )
    return {
        "scope": "synthetic_simple_revisions_and_classic_comments_only",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "details": details,
        "not_claimed": [
            "full OOXML XSD validation",
            "modern comments support",
            "complex review structure support",
            "Microsoft Word visual validation",
            "Windows validation",
            "real clinical document readiness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.source.read_bytes(), args.output.read_bytes(), _load_plan(args.plan))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
