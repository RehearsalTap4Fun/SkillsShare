"""Read drawing relationships without re-saving (and losing) workbook metadata."""
import hashlib
import posixpath
import zipfile
from xml.etree import ElementTree as ET

R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def column(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def inspect_xlsx(path):
    result = {'schemaVersion': 1, 'sheets': [], 'images': [], 'unsupported': []}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def xml(part):
            return ET.fromstring(z.read(part))

        def relations(part):
            relfile = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
            if relfile not in names:
                return {}
            return {r.attrib['Id']: posixpath.normpath(posixpath.join(posixpath.dirname(part), r.attrib['Target'])).lstrip('/')
                    for r in xml(relfile) if r.attrib.get('TargetMode') != 'External'}

        def local(tag):
            return tag.rsplit('}', 1)[-1]

        workbook = 'xl/workbook.xml'
        rels = relations(workbook)
        used_media = set()
        for sheet in xml(workbook).iter():
            if local(sheet.tag) != 'sheet':
                continue
            info = {'sheetId': sheet.attrib['sheetId'], 'name': sheet.attrib['name']}
            result['sheets'].append(info)
            part = rels.get(sheet.attrib.get(R + 'id'))
            if not part or part not in names:
                result['unsupported'].append({'sheet': info, 'reason': 'missing_sheet_relationship'})
                continue
            sheetrels = relations(part)
            for drawing in xml(part).iter():
                if local(drawing.tag) not in {'drawing', 'legacyDrawing'}:
                    continue
                drawingpart = sheetrels.get(drawing.attrib.get(R + 'id'))
                if not drawingpart or drawingpart not in names:
                    result['unsupported'].append({'part': part, 'reason': 'missing_drawing'})
                    continue
                drawrels = relations(drawingpart)
                for anchor in xml(drawingpart):
                    start = next((e for e in anchor if local(e.tag) == 'from'), None)
                    blips = [e for e in anchor.iter() if local(e.tag) == 'blip']
                    if start is None or not blips:
                        result['unsupported'].append({'part': drawingpart, 'reason': 'unknown_anchor'})
                        continue
                    coords = {local(e.tag): e.text for e in start}
                    cell = column(int(coords['col']) + 1) + str(int(coords['row']) + 1)
                    for blip in blips:
                        media = drawrels.get(blip.attrib.get(R + 'embed'))
                        if not media or media not in names:
                            result['unsupported'].append({'part': drawingpart, 'cell': cell, 'reason': 'missing_media'})
                            continue
                        used_media.add(media)
                        result['images'].append({**info, 'cell': cell, 'part': media, 'sha256': hashlib.sha256(z.read(media)).hexdigest()})
        for part in sorted(names):
            if part.startswith('xl/richData/') or 'cellimages' in part.lower() or (part.startswith('xl/media/') and part not in used_media):
                result['unsupported'].append({'part': part, 'reason': 'unmapped_image_storage'})
    return result
