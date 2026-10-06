"""
Collecte les alertes LinkedIn depuis Gmail et extrait les offres d'emploi.
"""

import os
import base64
import re
import json
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"

# Pause entre deux messages.get — Gmail plafonne à 250 unités/s/utilisateur
# et messages.get coûte 5 unités (~50 appels/s max). 0.05s laisse une marge
# confortable tout en restant rapide (~20 emails/s).
GMAIL_GET_DELAY = 0.05


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
            try:
                creds.refresh(Request())
            except Exception:
                os.remove(TOKEN_PATH)
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _execute_with_retry(request, what: str, max_retries: int = 6):
    """
    Exécute une requête Gmail en réessayant sur les erreurs de quota (403/429).

    Gmail applique une limite de 250 unités/seconde/utilisateur ; messages.get coûte
    5 unités. Sur une fenêtre longue (--days 31), la boucle de récupération dépasse
    cette limite et l'API renvoie 403 rateLimitExceeded. On réessaie en backoff
    exponentiel plutôt que de laisser le scan complet échouer.
    """
    delay = 2.0
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            retryable = status in (403, 429, 500, 503)
            # 403 couvre aussi des erreurs définitives (permission) : on ne réessaie
            # que si le motif est bien lié au quota / rate limit.
            if status == 403:
                reason = str(e)
                retryable = ("rateLimitExceeded" in reason
                             or "userRateLimitExceeded" in reason
                             or "Quota exceeded" in reason)
            if not retryable or attempt == max_retries - 1:
                raise
            print(f"[Gmail] Quota atteint sur {what} — pause {delay:.0f}s "
                  f"(tentative {attempt + 1}/{max_retries})")
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    return None


def _get_message_with_retry(service, msg_id: str) -> Optional[dict]:
    """Récupère un message ; renvoie None si l'email est inaccessible (supprimé…)."""
    req = service.users().messages().get(userId="me", id=msg_id, format="full")
    try:
        return _execute_with_retry(req, f"message {msg_id}")
    except HttpError as e:
        # Un email isolé introuvable ne doit pas faire échouer tout le scan
        if getattr(e.resp, "status", None) == 404:
            print(f"[Gmail] Message {msg_id} introuvable — ignoré")
            return None
        raise


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
        result = _execute_with_retry(
            service.users().messages().list(**params), "liste des emails"
        )
        messages.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    emails = []
    total = len(messages)
    for i, msg in enumerate(messages):
        full = _get_message_with_retry(service, msg["id"])
        if full:
            emails.append(full)
        # Throttle : Gmail limite à 250 unités/s/utilisateur et messages.get coûte
        # 5 unités. Sans pause, un scan long (--days 31) dépasse le quota → 403.
        if i + 1 < total:
            time.sleep(GMAIL_GET_DELAY)

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

    # Lignes parasites (entête email LinkedIn, call-to-action, compteurs)
    skip_patterns = re.compile(
        r"votre alerte|votre offre.{0,30}(enregistr|rappel)|nouvelle.{0,10}offre|correspond|préférence|"
        r"démarquez|recruteur|linkedin\.com|voir (toutes|l'offre)|see all|postuler maintenant|"
        r"relations?\s*$|\d+\s+relations?|élargissez votre recherche|recommandations bas|"
        r"\d+\s*anciens?\s*élèves?|\d+\s*alumni|\d+\s*candidat|actively recruiting|"
        r"offres d.emploi\b|postulez avec|candidature simplifiée|easy apply|"
        r"^(il y a|nouveau|promu|promoted|new)\b",
        re.IGNORECASE
    )

    # Chaque offre réelle est ancrée sur son lien "Voir l'offre d'emploi : <url>".
    # On itère sur ces ancres plutôt que sur des blocs : LinkedIn insère parfois
    # l'entête et la 1re offre dans le même bloc, et place aussi des IDs d'offres
    # dans des paramètres de tracking (originToLandingJobPostings=ID1,ID2) — se
    # fier au 1er ID du bloc faisait alors prendre l'entête pour le titre.
    anchor_re = re.compile(
        r"(?:Voir l.offre d.emploi|See job|View job)\s*:?\s*"
        r"https://www\.linkedin\.com/(?:comm/)?jobs/view/(\d+)",
        re.IGNORECASE
    )

    cursor = 0  # début des lignes descriptives de l'offre courante
    for i, m in enumerate(anchor_re.finditer(text)):
        job_id = m.group(1)
        segment = text[cursor:m.start()]
        cursor = m.end()

        canonical_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        if canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)

        # Titre / entreprise / localisation = les 3 dernières lignes utiles
        # juste avant l'ancre (et non les premières du bloc).
        info_lines = [
            l.strip() for l in segment.splitlines()
            if l.strip()
            and "linkedin.com" not in l.lower()
            and not skip_patterns.search(l.strip())
        ][-3:]

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
