import json
import os
import re
import sys
import tempfile
from datetime import date, datetime

import pdfplumber
import requests
from bs4 import BeautifulSoup

CAMPUSES = [
    ("Darcy Ribeiro", "https://ru.unb.br/cardapio/"),
    ("Ceilândia", "https://ru.unb.br/cardapio-ceilandia/"),
    ("Gama", "https://ru.unb.br/cardapio-gama/"),
    ("Planaltina", "https://ru.unb.br/cardapio-planaltina/"),
    ("Fazenda", "https://ru.unb.br/cardapio-fazenda-agua-limpa/"),
]

# Layout antigo: uma página central com os PDFs de todos os campi
MENU_PAGE_URL = "https://ru.unb.br/cardapio-refeitorio/"

# Como identificar o campus pelo nome do arquivo do PDF (layout antigo)
CAMPUS_FILE_PREFIX = {
    "Darcy Ribeiro": ("darcy",),
    "Ceilândia": ("ceilandia", "ceilândia"),
    "Gama": ("gama",),
    "Planaltina": ("planaltina",),
    "Fazenda": ("fazenda",),
}

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Rótulos do PDF (maiúsculas) -> rótulos usados pelo app
LABELS = {
    "BEBIDAS": "Bebidas",
    "PANIFICAÇÃO": "Panificação",
    "PANIFICACAO": "Panificação",
    "OPÇÃO EXTRA": "Opção extra",
    "OPCAO EXTRA": "Opção extra",
    "GORDURA": "Gordura",
    "COMPLEMENTO PADRÃO": "Complemento padrão",
    "COMPLEMENTO PADRAO": "Complemento padrão",
    "COMPLEMENTO OVOLACTOVEGETARIANO": "Complemento ovolactovegetariano",
    "COMPLEMENTO VEGETARIANO ESTRITO": "Complemento vegetariano estrito",
    "FRUTA": "Fruta",
    "SALADA 1": "Salada 1",
    "SALADA 2": "Salada 2",
    "MOLHO PARA SALADA": "Molho para salada",
    "PRATO PRINCIPAL PADRÃO": "Prato principal padrão",
    "PRATO PRINCIPAL PADRAO": "Prato principal padrão",
    "PRATO PRINCIPAL OVOLACTOVEGETARIANO": "Prato principal ovolactovegetariano",
    "PRATO PRINCIPAL VEGETARIANO ESTRITO": "Prato principal vegetariano estrito",
    "GUARNIÇÃO": "Guarnição",
    "GUARNICAO": "Guarnição",
    "ACOMPANHAMENTOS": "Acompanhamentos",
    "SOBREMESA": "Sobremesa",
    "BEBIDA (REFRESCO DE)": "Bebida (refresco de)",
    "SOPA": "Sopa",
    "TORRADA": "Torrada",
}


def normalize(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_label(value):
    raw = normalize(value).upper()
    if raw in LABELS:
        return LABELS[raw]
    # fallback: deixa no estilo do app (primeira letra maiúscula)
    return normalize(value).capitalize()


def meal_kind(table_rows):
    joined = " ".join(normalize(c) for row in table_rows for c in row).upper()
    if "SOPA" in joined or "TORRADA" in joined:
        return "jantar"
    if "GUARNI" in joined:
        return "almoco"
    return "desjejum"


def parse_meal_page(page):
    tables = [t for t in page.extract_tables() if t]
    if not tables:
        return {}, []
    table = tables[0]

    date_row_idx = None
    date_cols = []
    for ri, row in enumerate(table):
        cols = [ci for ci, c in enumerate(row) if normalize(c) and DATE_RE.match(normalize(c))]
        if len(cols) >= 3:
            date_row_idx = ri
            date_cols = cols
            break

    if date_row_idx is None:
        return {}, []

    dates = [normalize(table[date_row_idx][ci]) for ci in date_cols]
    label_col = date_cols[0] - 1

    result = {d: {} for d in dates}
    current_label = None

    for row in table[date_row_idx + 1 :]:
        label = normalize(row[label_col]) if label_col < len(row) else ""
        values = [
            normalize(row[ci]) if ci < len(row) else ""
            for ci in date_cols
        ]

        if label:
            current_label = normalize_label(label)
            if current_label not in result[dates[0]]:
                for d in dates:
                    result[d][current_label] = ""

        if not current_label:
            continue

        for d, value in zip(dates, values):
            if value:
                existing = result[d][current_label]
                result[d][current_label] = (
                    f"{existing} / {value}" if existing else value
                )

    return result, dates


def parse_pdf(path):
    meals = {"desjejum": {}, "almoco": {}, "jantar": {}}
    all_dates = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = [t for t in page.extract_tables() if t]
            if not tables:
                continue
            kind = meal_kind(tables[0])
            data, dates = parse_meal_page(page)
            if not dates:
                continue
            meals[kind] = data
            if len(dates) > len(all_dates):
                all_dates = dates

    rows = []
    for d in all_dates:
        iso = datetime.strptime(d, "%d/%m/%Y").strftime("%Y-%m-%d")
        rows.append(
            {
                "date": iso,
                "desjejum": meals["desjejum"].get(d, {}),
                "almoco": meals["almoco"].get(d, {}),
                "jantar": meals["jantar"].get(d, {}),
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def _date_key_from_text(text, href):
    dates = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if dates:
        day, month, year = dates[0]
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})-(\d{1,2})-a-(\d{1,2})-(\d{1,2})", href)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            return datetime(date.today().year, month, day)
        except ValueError:
            return None
    return None


def pdf_candidates(html):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        key = _date_key_from_text(a.get_text(" ", strip=True), href)
        if key:
            candidates.append((key, href))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


def pick_pdf_url(html):
    candidates = pdf_candidates(html)
    return candidates[0][1] if candidates else None


def build_fallback_map():
    """Lê a página central antiga e mapeia campus -> PDF mais recente."""
    try:
        resp = requests.get(MENU_PAGE_URL, timeout=60, verify=False)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[aviso] pagina central indisponivel: {exc}", file=sys.stderr)
        return {}

    latest = {}
    for key, href in pdf_candidates(resp.text):
        low = href.lower()
        for campus, prefixes in CAMPUS_FILE_PREFIX.items():
            if any(prefix in low for prefix in prefixes):
                if campus not in latest or key > latest[campus][0]:
                    latest[campus] = (key, href)
                break
    return {campus: value[1] for campus, value in latest.items()}


def download_and_parse(pdf_url):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pdf_resp = requests.get(pdf_url, timeout=120, verify=False)
        pdf_resp.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(pdf_resp.content)
        return parse_pdf(tmp_path)
    finally:
        os.unlink(tmp_path)


def fetch_campus(campus, url, fallback_href=None):
    errors = []
    try:
        resp = requests.get(url, timeout=60, verify=False)
        resp.raise_for_status()
        pdf_url = pick_pdf_url(resp.text)
        if pdf_url:
            return pdf_url, download_and_parse(pdf_url)
        errors.append("pagina do campus sem PDF")
    except Exception as exc:
        errors.append(str(exc))

    if fallback_href:
        try:
            return fallback_href, download_and_parse(fallback_href)
        except Exception as exc:
            errors.append(f"fallback: {exc}")

    raise RuntimeError("; ".join(errors))


def main():
    requests.packages.urllib3.disable_warnings()
    fallback_pdfs = build_fallback_map()
    result = {}
    for campus, url in CAMPUSES:
        try:
            pdf_url, rows = fetch_campus(campus, url, fallback_pdfs.get(campus))
            result[campus] = rows
            print(f"[ok] {campus}: {len(rows)} dias ({pdf_url})", file=sys.stderr)
        except Exception as exc:
            print(f"[erro] {campus}: {exc}", file=sys.stderr)
            result[campus] = []

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "menus.json")

    today = date.today()
    has_current = any(
        date.fromisoformat(row["date"]) >= today
        for rows in result.values()
        for row in rows
    )
    if not has_current:
        print(
            "ERRO: nenhum cardápio para hoje/futuro encontrado. "
            "Mantendo o JSON anterior.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Escrito: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
