"""
Collecte les alertes LinkedIn depuis Gmail et extrait les offres d'emploi.
"""

import os
import base64
import re
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

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


@dataclass
class JobOffer:
    id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str = "linkedin_email"
    salary: str = ""
    raw_html: str = ""
    email_date: str = ""   # date de l'email LinkedIn (YYYY-MM-DD)
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def get_gmail_service():
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

    return build("gmail", "v1", credentials=creds)


def fetch_linkedin_alert_emails(service, days_back: int = 1, skip_days: int = 0) -> list[dict]:
    """Récupère les emails d'alertes LinkedIn dans la fenêtre [J-days_back, J-skip_days]."""
    since = datetime.now(timezone.utc) - timedelta(days=days_back)
    since_str = since.strftime("%Y/%m/%d")

    # Couvre les 3 types d'emails LinkedIn avec des offres :
    # - jobalerts-noreply : alertes classiques
    # - jobs-listings : recommandations "ce poste pourrait vous convenir"
    # - jobs-noreply : "nouvelles offres similaires à X"
    query = f'from:(jobalerts-noreply@linkedin.com OR jobs-listings@linkedin.com OR jobs-noreply@linkedin.com) after:{since_str}'

    # Filtre "before" si fenêtre glissante
    if skip_days > 0:
        before = datetime.now(timezone.utc) - timedelta(days=skip_days)
        before_str = before.strftime("%Y/%m/%d")
        query += f' before:{before_str}'

    messages = []
    page_token = None
    while True:
        params = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        result = service.users().messages().list(**params).execute()
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    emails = []
    for msg in messages:
        full = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        emails.append(full)

    return emails


def decode_email_body(message: dict) -> str:
    """Décode le corps texte ou HTML d'un email Gmail — préfère text/plain."""
    payload = message.get("payload", {})

    def extract_parts(part, preferred="text/plain"):
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")
        if body_data and mime == preferred:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
        for subpart in part.get("parts", []):
            result = extract_parts(subpart, preferred)
            if result:
                return result
        return ""

    # Essaie text/plain d'abord (format réel des alertes LinkedIn), puis text/html
    text = extract_parts(payload, "text/plain")
    if not text:
        text = extract_parts(payload, "text/html")
    return text


def parse_jobs_from_text(text: str, email_id: str) -> list[JobOffer]:
    """
    Extrait les offres depuis le texte brut d'un email LinkedIn.
    Format réel : titre, entreprise, ville sur des lignes séparées,
    puis "Voir l'offre d'emploi : https://www.linkedin.com/comm/jobs/view/ID/..."
    """
    jobs = []
    seen_urls = set()

    # Découpe en blocs par séparateur "---..." ou ligne vide multiple
    blocks = re.split(r'-{5,}|\n{3,}', text)

    for i, block in enumerate(blocks):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue

        # Cherche l'URL LinkedIn jobs dans le bloc
        url_match = re.search(
            r'https://www\.linkedin\.com/comm/jobs/view/(\d+)/[^\s]*',
            block
        )
        if not url_match:
            # Pas d'URL job dans ce bloc, ignore
            continue

        job_id = url_match.group(1)
        canonical_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        if canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)

        # Les lignes avant "Voir l'offre" contiennent titre, entreprise, localisation
        # Filtre les lignes parasites (entête email LinkedIn)
        skip_patterns = re.compile(
            r"votre alerte|nouvelle.{0,10}offre|correspond|préférence|"
            r"démarquez|recruteur|linkedin\.com|voir (toutes|l'offre)|see all",
            re.IGNORECASE
        )
        info_lines = []
        for line in lines:
            if "linkedin.com" in line.lower() or line.lower().startswith("voir") or line.lower().startswith("see"):
                break
            if not skip_patterns.search(line):
                info_lines.append(line)

        title = info_lines[0] if len(info_lines) > 0 else ""
        company = info_lines[1] if len(info_lines) > 1 else ""
        location = info_lines[2] if len(info_lines) > 2 else ""

        # Si titre vide ou parasite, skip
        if not title or len(title) < 3:
            continue

        jobs.append(JobOffer(
            id=f"{email_id}_{i}",
            title=title,
            company=company,
            location=location,
            description="",
            url=canonical_url,
            email_date="",  # rempli par collect_jobs_from_gmail
        ))

    return jobs


def collect_jobs_from_gmail(days_back: int = 1, skip_days: int = 0) -> list[JobOffer]:
    """Point d'entrée principal : retourne toutes les offres des alertes LinkedIn."""
    service = get_gmail_service()
    emails = fetch_linkedin_alert_emails(service, days_back=days_back, skip_days=skip_days)

    all_jobs: list[JobOffer] = []
    for email in emails:
        text = decode_email_body(email)
        jobs = parse_jobs_from_text(text, email_id=email["id"])
        # Injecte la date de l'email dans chaque offre
        internal_date_ms = int(email.get("internalDate", 0))
        if internal_date_ms:
            email_date = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            email_date = ""
        for job in jobs:
            job.email_date = email_date
        all_jobs.extend(jobs)

    # Dédoublonnage par URL
    seen = set()
    unique_jobs = []
    for job in all_jobs:
        if job.url not in seen:
            seen.add(job.url)
            unique_jobs.append(job)

    print(f"[Gmail] {len(emails)} email(s) traité(s) → {len(unique_jobs)} offre(s) unique(s)")
    return unique_jobs


if __name__ == "__main__":
    jobs = collect_jobs_from_gmail(days_back=1)
    for job in jobs:
        print(json.dumps(job.to_dict(), ensure_ascii=False, indent=2))
