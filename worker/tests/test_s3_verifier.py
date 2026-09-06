from __future__ import annotations

import io
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from lxml import etree

from clinical_qc.docx_package import COMMENTS, MAIN, NS, w
from scripts.verify_s3_native_review import _load_plan, verify


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/verification/fixtures/s3/synthetic-native-review-source-v0.1.docx"
OUTPUT = ROOT / "docs/verification/artifacts/s3/synthetic-native-review-redline-v0.1.docx"
PLAN = ROOT / "docs/verification/evidence/2026-09-05-s3-macos/frozen-change-plan.json"


def _rewrite_part(content: bytes, part_name: str, mutate) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = [(deepcopy(info), archive.read(info.filename)) for info in archive.infolist()]
    rewritten = io.BytesIO()
    with zipfile.ZipFile(rewritten, "w") as archive:
        for info, payload in entries:
            if info.filename == part_name:
                root = etree.fromstring(payload)
                mutate(root)
                payload = etree.tostring(
                    root.getroottree(),
                    encoding="UTF-8",
                    xml_declaration=True,
                    standalone=True,
                )
            archive.writestr(info, payload)
    return rewritten.getvalue()


class S3VerifierTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_bytes()
        cls.output = OUTPUT.read_bytes()
        cls.plan = _load_plan(PLAN)

    def test_frozen_fixture_passes(self) -> None:
        report = verify(self.source, self.output, self.plan)
        self.assertEqual(report["status"], "passed")

    def test_changed_comment_body_fails_closed(self) -> None:
        def mutate(root: etree._Element) -> None:
            text = root.xpath(
                "./w:comment[@w:id='15']//w:t", namespaces=NS
            )[0]
            text.text = "被篡改的批注"

        changed = _rewrite_part(self.output, COMMENTS, mutate)
        report = verify(self.source, changed, self.plan)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["new_comment_content_and_anchor_match_plan"])

    def test_duplicate_comment_reference_fails_closed(self) -> None:
        def mutate(root: etree._Element) -> None:
            reference = root.xpath(
                ".//w:commentReference[@w:id='15']", namespaces=NS
            )[0]
            reference.getparent().append(deepcopy(reference))

        changed = _rewrite_part(self.output, MAIN, mutate)
        report = verify(self.source, changed, self.plan)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["comment_anchors_are_one_to_one"])

    def test_changed_or_unconfirmed_plan_fails_closed(self) -> None:
        changed_comment = replace(
            self.plan.changes[1],
            comment="被篡改的计划批注",
            user_confirmed=False,
        )
        changed_plan = replace(
            self.plan,
            changes=(self.plan.changes[0], changed_comment),
        )
        report = verify(self.source, self.output, changed_plan)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["frozen_plan_hash_and_confirmations_valid"])
        self.assertFalse(report["checks"]["new_comment_content_and_anchor_match_plan"])
