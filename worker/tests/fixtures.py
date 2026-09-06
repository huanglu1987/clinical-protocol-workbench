"""Synthetic in-memory OPC fixtures; never sourced from clinical documents."""

import io
import zipfile

from clinical_qc.docx_package import COMMENT_TYPE, CT, DOC_TYPE, R, REL, W


SETTINGS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"


def paragraph(text: str) -> str:
    from xml.sax.saxutils import escape
    return ('<w:p><w:r w:rsidR="00000001"><w:rPr><w:b/><w:color w:val="123456"/>'
            '</w:rPr><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>')


def make_docx(body: str, *, history: bool = False, extras: dict[str, bytes] | None = None,
              comment_override: str | None = None, rel_extra: str = "") -> bytes:
    history_xml = '''<w:p><w:del w:id="7" w:author="原审阅者" w:date="2026-08-01T00:00:00Z">
<w:r><w:delText>旧文字</w:delText></w:r></w:del>
<w:ins w:id="8" w:author="原审阅者" w:date="2026-08-01T00:00:00Z">
<w:r><w:t>待接受文字</w:t></w:r></w:ins></w:p>
<w:p><w:commentRangeStart w:id="12"/><w:r><w:t>已有批注位置</w:t></w:r>
<w:commentRangeEnd w:id="12"/><w:r><w:commentReference w:id="12"/></w:r></w:p>'''
    document = f'<w:document xmlns:w="{W}"><w:body>{body}{history_xml if history else ""}<w:sectPr/></w:body></w:document>'
    comment_type = f'<Override PartName="/word/comments.xml" ContentType="{COMMENT_TYPE}"/>' if history else ""
    types = (f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             f'<Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/>'
             f'<Override PartName="/word/document.xml" ContentType="{DOC_TYPE}"/>'
             f'<Override PartName="/word/settings.xml" ContentType="{SETTINGS_TYPE}"/>{comment_type}</Types>')
    comment_link = f'<Relationship Id="rId10" Type="{R}/comments" Target="comments.xml"/>' if history else ""
    settings_link = f'<Relationship Id="rId9" Type="{R}/settings" Target="settings.xml"/>'
    parts = {
        "[Content_Types].xml": types.encode(),
        "_rels/.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'.encode(),
        "word/document.xml": document.encode(),
        "word/_rels/document.xml.rels": f'<Relationships xmlns="{REL}">{settings_link}{comment_link}{rel_extra}</Relationships>'.encode(),
        "word/settings.xml": f'<w:settings xmlns:w="{W}"><w:trackRevisions/></w:settings>'.encode(),
        "customXml/item1.xml": b'<source untouched="yes">preserve exact bytes</source>',
    }
    if history:
        parts["word/comments.xml"] = (comment_override or f'<w:comments xmlns:w="{W}"><w:comment w:id="12" w:author="原审阅者" w:date="2026-08-01T00:00:00Z"><w:p><w:r><w:t>不要删除此批注。</w:t></w:r></w:p></w:comment></w:comments>').encode()
    if extras:
        parts.update(extras)
    return pack(parts)


def pack(parts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"synthetic-test-fixture"
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()
