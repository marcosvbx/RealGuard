"""
Planilhas .xlsx sem dependências (só a biblioteca padrão do Python 3.8).

A AIBOX não tem internet fora da feira e o ambiente do professor não traz
openpyxl, então o RealGuard escreve e lê o formato direto: um .xlsx é um zip
de XMLs. O suficiente para o que o sistema faz:

* escrever: várias abas, cabeçalho destacado e congelado, filtro, largura das
  colunas, colunas de texto (horários e RMs não viram número no Excel),
  colunas só de leitura em cinza e listas de opções (validação de dados);
* ler: o que o Excel, o LibreOffice e o Google Planilhas salvam (textos
  compartilhados, texto embutido, números, booleanos), aba por aba.

Datas e horários digitados pelo usuário podem chegar como número de série do
Excel: quem lê converte pela coluna (ver app/school/planilha.py).
"""

import io
import re
import zipfile
from typing import Any, Dict, List, Optional, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

MAX_UNCOMPRESSED = 60 * 1024 * 1024     # 60 MB descompactado: planilha de escola fica muito abaixo
MAX_ROWS = 20000

_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class XlsxError(ValueError):
    pass


# ================================================================== escrita
class Column:
    """kind: text | int | num1 (1 casa) | pct (número 0-100 com 1 casa). readonly = cinza, ignorada na leitura."""

    def __init__(self, title: str, width: float = 16, kind: str = "text", readonly: bool = False,
                 choices: Optional[Sequence[str]] = None, choices_ref: str = "", strict: bool = True):
        self.title, self.width, self.kind, self.readonly = title, width, kind, readonly
        self.choices, self.choices_ref, self.strict = list(choices or []), choices_ref, strict


class Sheet:
    def __init__(self, name: str, columns: List[Column], rows: List[List[Any]], tab_color: str = "",
                 notes: Optional[List[str]] = None):
        self.name, self.columns, self.rows, self.tab_color = name[:31], columns, rows, tab_color
        self.notes = notes or []      # aba de texto (Leia-me): uma linha por item, sem colunas


# estilos (índices de cellXfs em _STYLES)
_S_DEFAULT, _S_HEAD, _S_TEXT, _S_RO, _S_TITLE, _S_NOTE, _S_INT, _S_NUM1, _S_HEAD_RO, _S_SUB = range(10)

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>
<fonts count="6">
<font><sz val="11"/><color rgb="FF0B2447"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>
<font><i/><sz val="11"/><color rgb="FF5B6B85"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="16"/><color rgb="FF0B2447"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="11"/><color rgb="FFD6E2F5"/><name val="Calibri"/><family val="2"/></font>
<font><b/><sz val="12"/><color rgb="FF0B2447"/><name val="Calibri"/><family val="2"/></font>
</fonts>
<fills count="5">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF0B2447"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF1F3F7"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF283E5C"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="10">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="49" fontId="2" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="49" fontId="4" fillId="4" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="49" fontId="5" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _x(value: Any) -> str:
    return escape(_BAD_CHARS.sub("", str(value)), {'"': "&quot;"})


def _text_cell(ref: str, value: Any, style: int) -> str:
    text = "" if value is None else str(value)
    if text == "":
        return f'<c r="{ref}" s="{style}"/>'
    space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t{space}>{_x(text)}</t></is></c>'


def _cell(ref: str, value: Any, col: Column) -> str:
    if col.kind in ("int", "num1", "pct") and isinstance(value, (int, float)) and not isinstance(value, bool) \
            and not col.readonly:
        style = _S_INT if col.kind == "int" else _S_NUM1
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return _text_cell(ref, value, _S_RO if col.readonly else _S_TEXT)


def _quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _sheet_xml(sh: Sheet) -> str:
    if sh.notes is not None and not sh.columns:
        return _notes_xml(sh)
    ncols = len(sh.columns)
    last = col_letter(max(ncols - 1, 0))
    nrows = len(sh.rows) + 1
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{c.width}" customWidth="1" '
        f'style="{_S_RO if c.readonly else (_S_TEXT if c.kind == "text" else _S_DEFAULT)}"/>'
        for i, c in enumerate(sh.columns))
    head = "".join(_text_cell(f"{col_letter(i)}1", c.title, _S_HEAD_RO if c.readonly else _S_HEAD)
                   for i, c in enumerate(sh.columns))
    body = [f'<row r="1" ht="22" customHeight="1">{head}</row>']
    for r, row in enumerate(sh.rows, start=2):
        cells = "".join(_cell(f"{col_letter(i)}{r}", row[i] if i < len(row) else "", c)
                        for i, c in enumerate(sh.columns))
        body.append(f'<row r="{r}">{cells}</row>')
    validations = []
    top = max(nrows + 500, 1000)   # deixa espaço para linhas novas
    for i, c in enumerate(sh.columns):
        if c.readonly or not (c.choices or c.choices_ref):
            continue
        formula = c.choices_ref if c.choices_ref else '"' + ",".join(c.choices).replace('"', "") + '"'
        style = "stop" if c.strict else "information"
        validations.append(
            f'<dataValidation type="list" errorStyle="{style}" allowBlank="1" showInputMessage="1" '
            f'showErrorMessage="1" errorTitle="Valor fora da lista" error="Escolha um valor da lista." '
            f'sqref="{col_letter(i)}2:{col_letter(i)}{top}"><formula1>{_x(formula)}</formula1></dataValidation>')
    dv = f'<dataValidations count="{len(validations)}">{"".join(validations)}</dataValidations>' if validations else ""
    tab = f'<sheetPr><tabColor rgb="FF{sh.tab_color}"/></sheetPr>' if sh.tab_color else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'{tab}<dimension ref="A1:{last}{nrows}"/>'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
            f'<sheetFormatPr defaultRowHeight="15"/><cols>{cols}</cols>'
            f'<sheetData>{"".join(body)}</sheetData>'
            f'<autoFilter ref="A1:{last}{nrows}"/>{dv}'
            '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
            '</worksheet>')


def _notes_xml(sh: Sheet) -> str:
    rows = []
    for r, line in enumerate(sh.notes, start=1):
        if r == 1:
            style = _S_TITLE
        elif line.startswith("## "):
            style, line = _S_SUB, line[3:]
        else:
            style = _S_NOTE
        rows.append(f'<row r="{r}">{_text_cell(f"A{r}", line, style)}</row>')
    tab = f'<sheetPr><tabColor rgb="FF{sh.tab_color}"/></sheetPr>' if sh.tab_color else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{tab}<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
            '<cols><col min="1" max="1" width="120" customWidth="1"/></cols>'
            f'<sheetData>{"".join(rows)}</sheetData></worksheet>')


def write_xlsx(sheets: List[Sheet], title: str = "RealGuard") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + '<Override PartName="/docProps/core.xml" '
                     'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                   'metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
                   'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:title>{_x(title)}</dc:title><dc:creator>RealGuard</dc:creator></cp:coreProperties>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
                             f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/'
                     'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        names = "".join(f'<sheet name="{_x(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
                        for i, s in enumerate(sheets, start=1))
        defined = "".join(
            f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
            f'{_x(_quote_sheet(s.name))}!$A$1:${col_letter(len(s.columns) - 1)}${len(s.rows) + 1}</definedName>'
            for i, s in enumerate(sheets) if s.columns)
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<bookViews><workbookView activeTab="0"/></bookViews>'
                   f'<sheets>{names}</sheets>'
                   + (f"<definedNames>{defined}</definedNames>" if defined else "")
                   + '</workbook>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(s))
    return buf.getvalue()


def sheet_ref(sheet_name: str, column_index: int, last_row: int = 3000) -> str:
    """Intervalo de uma coluna de outra aba, para listas de opções (ex.: turnos já cadastrados)."""
    c = col_letter(column_index)
    return f"{_quote_sheet(sheet_name)}!${c}$2:${c}${last_row}"


# ================================================================== leitura
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(data: bytes):
    head = data[:4096].decode("utf-8", "ignore").upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in data.decode("utf-8", "ignore").upper():
        raise XlsxError("Planilha com conteúdo não permitido.")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise XlsxError("Planilha danificada: não consegui ler o arquivo.")


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _number_text(v: str) -> str:
    """'566217' fica '566217'; '0.3125' fica como está (quem lê sabe se é horário)."""
    try:
        f = float(v)
    except ValueError:
        return v
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def read_xlsx(content: bytes) -> Dict[str, List[Dict[str, Any]]]:
    """
    {nome da aba: [{"linha": nº, "celulas": [texto, ...]}, ...]} — linhas vazias de fora.
    Números vêm como texto ("566217", "0.3125"); booleanos como "1"/"0".
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise XlsxError("O arquivo não é uma planilha .xlsx (salve no Excel como \"Pasta de Trabalho do Excel\").")
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise XlsxError("Planilha grande demais.")
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise XlsxError("O arquivo não é uma planilha .xlsx (salve no Excel como \"Pasta de Trabalho do Excel\").")
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in _parse(z.read("xl/sharedStrings.xml")):
                if _local(si.tag) != "si":
                    continue
                parts = [t.text or "" for t in si.iter() if _local(t.tag) == "t" and not _in_phonetic(si, t)]
                shared.append("".join(parts))
        rels = {}
        rel_path = "xl/_rels/workbook.xml.rels"
        if rel_path in names:
            for r in _parse(z.read(rel_path)):
                target = r.get("Target", "")
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                rels[r.get("Id")] = target
        out: Dict[str, List[Dict[str, Any]]] = {}
        wb = _parse(z.read("xl/workbook.xml"))
        for el in wb.iter():
            if _local(el.tag) != "sheet":
                continue
            rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), "")
            path = rels.get(rid, "")
            if path not in names:
                continue
            out[el.get("name", "")] = _read_sheet(_parse(z.read(path)), shared)
        return out


def _in_phonetic(si, target) -> bool:
    for rph in si.iter():
        if _local(rph.tag) == "rPh" and any(t is target for t in rph.iter()):
            return True
    return False


def _read_sheet(root, shared: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        if len(rows) >= MAX_ROWS:
            raise XlsxError(f"Planilha com mais de {MAX_ROWS} linhas numa aba.")
        cells: Dict[int, str] = {}
        auto = 0
        for c in row:
            if _local(c.tag) != "c":
                continue
            ref = c.get("r")
            idx = _col_index(ref) if ref else auto
            auto = idx + 1
            t = c.get("t", "n")
            value = ""
            if t == "inlineStr":
                value = "".join(x.text or "" for x in c.iter() if _local(x.tag) == "t")
            else:
                v = next((x for x in c if _local(x.tag) == "v"), None)
                raw = v.text if v is not None and v.text is not None else ""
                if t == "s":
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = ""
                elif t in ("str", "b"):
                    value = raw
                elif t == "e":
                    value = ""
                else:
                    value = _number_text(raw) if raw else ""
            if value != "":
                cells[idx] = value
        if not cells:
            continue
        width = max(cells) + 1
        number = int(row.get("r")) if (row.get("r") or "").isdigit() else (rows[-1]["linha"] + 1 if rows else 1)
        rows.append({"linha": number, "celulas": [cells.get(i, "") for i in range(width)]})
    return rows
