"""
Écrit les offres scorées dans un Google Sheets dédié.
Crée l'onglet s'il n'existe pas, dédoublonne par URL, ajoute en haut.
"""

import os
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"

SHEET_NAME = "Job Scanner"
TAB_NAME = "Offres"

COLUMNS = [
    "Date offre",           # date de l'email LinkedIn (YYYY-MM-DD)
    "Date ajout",
    "Score P1 /10",         # score passe 1 (titre + entreprise + localisation)
    "Résumé P1",            # raison rejet P1 ou vide si GO
    "Accepté",              # TRUE si score >= seuil et non rejeté, FALSE sinon
    "Date scoring P2",      # date de la passe 2 (vide si non réalisée)
    "Score P2 /10",         # score passe 2 (vide si P2 non réalisée)
    "Recommandation P2",    # GO ou NO GO selon Claude en P2 (vide si P2 non réalisée)
    "Score Rôle",
    "Score Entreprise",
    "Score Localisation",
    "Titre",
    "Entreprise",
    "Localisation",
    "Salaire affiché",
    "Salaire estimé",       # estimé par Claude en passe 2
    "Résumé",               # analyse complète P2 (vide si P2 non réalisée)
    "Points forts",
    "Red flags",
    "Taille entreprise",
    "Secteur",
    "Séniorité",
    "Funding / Type",
    "Description entreprise",
    "Description offre",    # description complète récupérée via API
    "Dutch Required?",      # mandatory / preferred / vide si non mentionné (P2)
    "URL",
    "Source",
    "Statut",               # à remplir manuellement : Postulé / Pas intéressé / En cours
]


def get_sheets_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


def get_or_create_spreadsheet(service, spreadsheet_id: str = "") -> str:
    """Retourne l'ID du spreadsheet (crée si non fourni)."""
    if spreadsheet_id:
        return spreadsheet_id

    spreadsheet = service.spreadsheets().create(body={
        "properties": {"title": SHEET_NAME},
        "sheets": [{"properties": {"title": TAB_NAME}}],
    }).execute()

    sheet_id = spreadsheet["spreadsheetId"]
    print(f"[Sheets] Spreadsheet créé : https://docs.google.com/spreadsheets/d/{sheet_id}")
    return sheet_id


def ensure_header(service, spreadsheet_id: str):
    """Crée l'en-tête si la feuille est vide."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{TAB_NAME}!A1:A1"
    ).execute()

    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{TAB_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [COLUMNS]},
        ).execute()

        # Mise en forme : header en gras, fond bleu foncé
        sheet_meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_id_int = next(
            s["properties"]["sheetId"]
            for s in sheet_meta["sheets"]
            if s["properties"]["title"] == TAB_NAME
        )
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "repeatCell": {
                    "range": {"sheetId": sheet_id_int, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": {"red": 0.13, "green": 0.27, "blue": 0.53},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    }},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            }]}
        ).execute()


def get_existing_urls(service, spreadsheet_id: str) -> set:
    """Récupère les URLs déjà présentes pour éviter les doublons."""
    url_col_index = COLUMNS.index("URL")
    col_letter = chr(ord("A") + url_col_index)

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{TAB_NAME}!{col_letter}2:{col_letter}10000"
    ).execute()

    values = result.get("values", [])
    return {row[0] for row in values if row}


SHORTLIST_THRESHOLD = 6


def job_to_row(job: dict) -> list:
    """Convertit un job dict en ligne Google Sheets."""
    is_rejected = job.get("hard_reject", False)
    scored_p2 = job.get("scored_p2", False)  # True uniquement si la passe 2 a été exécutée
    score_p1 = job.get("score_p1", job.get("score_total", 0))
    score_p2 = job.get("score_total", 0) if scored_p2 else ""
    date_p2 = job.get("date_scoring_p2", "") if scored_p2 else ""

    effective_score = job.get("score_total", 0)
    accepted = not is_rejected and effective_score >= SHORTLIST_THRESHOLD

    # Résumé P1 : raison rejet si rejeté en P1 (pas de P2), vide sinon
    resume_p1 = job.get("reject_reason", "") if (is_rejected and not scored_p2) else ""

    # Résumé P2 : analyse complète Claude, uniquement si P2 réalisée
    resume_p2 = job.get("summary", "") if scored_p2 else ""

    # Recommandation P2 : priorité à la valeur retournée par Claude, sinon dérivée du score
    if scored_p2:
        reco_p2 = job.get("recommendation") or ("NO GO" if is_rejected else ("GO" if effective_score >= SHORTLIST_THRESHOLD else "NO GO"))
    else:
        reco_p2 = ""

    return [
        job.get("email_date", ""),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        score_p1,
        resume_p1,
        "TRUE" if accepted else "FALSE",
        date_p2,
        score_p2,
        reco_p2,
        job.get("score_role", "") if scored_p2 else "",
        job.get("score_company", "") if scored_p2 else "",
        job.get("score_location", "") if scored_p2 else "",
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("salary", ""),
        job.get("salary_estimate", "") if scored_p2 else "",
        resume_p2,
        job.get("strengths", "") if scored_p2 else "",
        job.get("red_flags", "") if scored_p2 else "",
        job.get("company_size", ""),
        job.get("company_industry", ""),
        job.get("seniority_level", ""),
        job.get("company_funding", ""),
        job.get("company_description", ""),
        job.get("description", ""),
        job.get("dutch_required", "") if scored_p2 else "",
        job.get("url", ""),
        job.get("source", ""),
        "",  # Statut — à remplir manuellement
    ]


def write_jobs_to_sheets(jobs: list[dict], spreadsheet_id: str = "") -> str:
    """
    Écrit les offres dans Google Sheets.
    Retourne l'URL du spreadsheet.
    """
    service = get_sheets_service()
    spreadsheet_id = get_or_create_spreadsheet(service, spreadsheet_id)
    ensure_header(service, spreadsheet_id)
    existing_urls = get_existing_urls(service, spreadsheet_id)

    new_jobs = [j for j in jobs if j.get("url") not in existing_urls]
    if not new_jobs:
        print("[Sheets] Aucune nouvelle offre à ajouter (toutes déjà présentes)")
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

    # Tri par score décroissant
    new_jobs.sort(key=lambda x: x.get("score_total", 0), reverse=True)
    rows = [job_to_row(j) for j in new_jobs]

    # Insère après le header (ligne 2) pour avoir les nouvelles offres en haut
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{TAB_NAME}!A2",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    print(f"[Sheets] {len(new_jobs)} nouvelle(s) offre(s) ajoutée(s)")
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
    print(f"[Sheets] {url}")
    return url
