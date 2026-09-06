"""Bounded, in-memory OPC reader. No extraction, network or source-file writes."""

from __future__ import annotations

import copy
import hashlib
import io
import posixpath
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R}
MAIN = "word/document.xml"
COMMENTS = "word/comments.xml"
DOC_RELS = "word/_rels/document.xml.rels"
DOC_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
COMMENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"


class RejectedDocument(ValueError):
    """Safe, stable error code without leaking source content."""


def w(local: str) -> str:
    return f"{{{W}}}{local}"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_xml(data: bytes) -> etree._Element:
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                                 recover=False, huge_tree=False, remove_blank_text=False)
        root = etree.fromstring(data, parser=parser)
        if root.getroottree().docinfo.doctype or any(isinstance(n, etree._Entity) for n in root.iter()):
            raise RejectedDocument("xml_dtd_or_entity")
        return root
    except etree.XMLSyntaxError as exc:
        raise RejectedDocument("invalid_xml") from exc


def serialize(root: etree._Element) -> bytes:
    # Serializing only the root drops preceding/following PIs and XML comments.
    return etree.tostring(root.getroottree(), encoding="UTF-8", xml_declaration=True, standalone=True)


def _check_inert_xml(root: etree._Element) -> None:
    if any(n.tag in {w("documentProtection"), w("writeProtection"), w("altChunk")}
           for n in root.iter()):
        raise RejectedDocument("protected_or_imported_content")


@dataclass(frozen=True)
class Limits:
    archive_bytes: int = 64 * 1024 * 1024
    unpacked_bytes: int = 256 * 1024 * 1024
    part_bytes: int = 32 * 1024 * 1024
    parts: int = 10000
    compression_ratio: int = 200


def _part_name(name: str) -> bool:
    return bool(name and not name.startswith("/") and "\\" not in name and ":" not in name
                and "%" not in name and not any(p in ("", ".", "..") for p in name.split("/")))


def relationship_target(rels_name: str, target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in target:
        raise RejectedDocument("unsupported_relationship_target")
    decoded = unquote(parsed.path)
    if rels_name == "_rels/.rels":
        base = ""
    else:
        parent, filename = posixpath.split(rels_name)
        if posixpath.basename(parent) != "_rels" or not filename.endswith(".rels"):
            raise RejectedDocument("invalid_relationship_part")
        base = posixpath.dirname(parent)
    resolved = posixpath.normpath(posixpath.join(base, decoded)) if not decoded.startswith("/") else decoded[1:]
    if not _part_name(resolved):
        raise RejectedDocument("relationship_path_escape")
    return resolved


class DocxPackage:
    def __init__(self, data: bytes, limits: Limits = Limits()):
        if not isinstance(data, bytes) or len(data) > limits.archive_bytes:
            raise RejectedDocument("archive_size_limit")
        self.source_hash = digest(data)
        entries: dict[str, bytes] = {}
        infos: list[zipfile.ZipInfo] = []
        names: set[str] = set()
        total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > limits.parts:
                    raise RejectedDocument("part_count_limit")
                self.archive_comment = archive.comment
                for info in archive.infolist():
                    name = info.filename
                    if info.is_dir():
                        raise RejectedDocument("directory_entry_unsupported")
                    if not _part_name(name):
                        raise RejectedDocument("unsafe_part_name")
                    canonical = unicodedata.normalize("NFC", name).casefold()
                    if canonical in names:
                        raise RejectedDocument("duplicate_part")
                    names.add(canonical)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if info.flag_bits & 1 or stat.S_ISLNK(mode):
                        raise RejectedDocument("encrypted_or_symlink_part")
                    total += info.file_size
                    if (info.file_size > limits.part_bytes or total > limits.unpacked_bytes
                            or info.file_size > max(info.compress_size, 1) * limits.compression_ratio):
                        raise RejectedDocument("unpacked_size_limit")
                    content = archive.read(info)
                    if len(content) != info.file_size:
                        raise RejectedDocument("part_size_mismatch")
                    entries[name] = content
                    infos.append(copy.copy(info))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
            raise RejectedDocument("invalid_zip") from exc
        self.parts = MappingProxyType(entries)
        self._infos = tuple(infos)
        self._validate()

    def xml(self, name: str) -> etree._Element:
        if name not in self.parts:
            raise RejectedDocument("missing_required_part")
        return parse_xml(self.parts[name])

    def _validate(self) -> None:
        if not {MAIN, "[Content_Types].xml", "_rels/.rels"} <= self.parts.keys():
            raise RejectedDocument("missing_required_part")
        for name, data in self.parts.items():
            if any(token in name.lower() for token in ("vbaproject", "_xmlsignatures/", "embeddings/")):
                raise RejectedDocument("active_or_signed_content")
            if name.endswith((".xml", ".rels")):
                root = parse_xml(data)
                _check_inert_xml(root)
        types = self.xml("[Content_Types].xml")
        if types.tag != f"{{{CT}}}Types":
            raise RejectedDocument("invalid_content_types")
        overrides: dict[str, str] = {}
        defaults: dict[str, str] = {}
        for item in types:
            kind = item.get("ContentType", "")
            if any(t in kind.lower() for t in ("macroenabled", "vbaproject", "digital-signature")):
                raise RejectedDocument("active_or_signed_content")
            if item.tag == f"{{{CT}}}Override":
                name = item.get("PartName", "")
                if not name.startswith("/") or name[1:] not in self.parts or name in overrides:
                    raise RejectedDocument("invalid_content_type_override")
                overrides[name] = kind
            elif item.tag == f"{{{CT}}}Default":
                extension = item.get("Extension", "").lower()
                if not extension or extension in defaults:
                    raise RejectedDocument("invalid_content_type_default")
                defaults[extension] = kind
            else:
                raise RejectedDocument("invalid_content_types")
        if overrides.get("/" + MAIN) != DOC_TYPE:
            raise RejectedDocument("not_supported_docx")
        content_types: dict[str, str] = {}
        for name in self.parts:
            if name != "[Content_Types].xml" and "/" + name not in overrides and name.rsplit(".", 1)[-1].lower() not in defaults:
                raise RejectedDocument("missing_content_type")
            content_types[name] = overrides.get("/" + name, defaults.get(name.rsplit(".", 1)[-1].lower(), ""))
        xml_parts = {name for name, kind in content_types.items()
                     if name.endswith((".xml", ".rels")) or kind.lower().endswith(("+xml", "/xml"))}
        for name in xml_parts:
            _check_inert_xml(self.xml(name))
        self.xml_parts = frozenset(xml_parts)
        for name in self.parts:
            if not name.endswith(".rels") and content_types[name] != "application/vnd.openxmlformats-package.relationships+xml":
                continue
            root = self.xml(name)
            if root.tag != f"{{{REL}}}Relationships":
                raise RejectedDocument("invalid_relationships")
            ids: set[str] = set()
            for item in root:
                rid = item.get("Id", "")
                if item.tag != f"{{{REL}}}Relationship" or not rid or rid in ids or not item.get("Type"):
                    raise RejectedDocument("invalid_relationships")
                ids.add(rid)
                if item.get("TargetMode", "Internal") != "Internal":
                    raise RejectedDocument("external_relationship_unsupported")
                target = relationship_target(name, item.get("Target", ""))
                if target not in self.parts:
                    raise RejectedDocument("dangling_relationship")
                xml_roles = {R + "/" + role for role in ("officeDocument", "settings", "styles", "webSettings",
                             "fontTable", "footnotes", "endnotes", "header", "footer", "numbering", "comments")}
                if item.get("Type") in xml_roles and target not in xml_parts:
                    raise RejectedDocument("xml_relationship_content_type_mismatch")
        roots = self.xml("_rels/.rels")
        office = [e for e in roots if e.get("Type") == R + "/officeDocument"]
        if len(office) != 1 or relationship_target("_rels/.rels", office[0].get("Target", "")) != MAIN:
            raise RejectedDocument("unsupported_main_part")
        document = self.xml(MAIN)
        if document.tag != w("document") or len(document.findall(w("body"))) != 1:
            raise RejectedDocument("invalid_document_body")

    def updated(self, replacements: dict[str, bytes]) -> bytes:
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as archive:
            archive.comment = self.archive_comment
            for info in self._infos:
                archive.writestr(copy.copy(info), replacements.get(info.filename, self.parts[info.filename]))
            for name, data in replacements.items():
                if name not in self.parts:
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        return result.getvalue()
