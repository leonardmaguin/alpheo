"""
Horodatage local — toutes les dates écrites dans le Sheets passent par ici.

Les colonnes Date ajout / Date P1 / Date P2 sont lues par un humain :
elles doivent afficher l'heure de Bruxelles/Paris, pas l'UTC.
Les dates envoyées à des API (requêtes Gmail) restent en UTC.
"""

from datetime import datetime, timezone

LOCAL_TZ_NAME = "Europe/Paris"  # = Europe/Brussels (même décalage toute l'année)

try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except Exception:  # pragma: no cover - tzdata absent (Windows sans paquet tzdata)
    # Repli : fuseau local de la machine. Préférable à l'UTC, qui afficherait
    # systématiquement 2h de moins en été.
    _LOCAL_TZ = None


def local_now() -> datetime:
    """datetime courant en heure de Paris/Bruxelles."""
    if _LOCAL_TZ is not None:
        return datetime.now(_LOCAL_TZ)
    return datetime.now().astimezone()


def now_stamp() -> str:
    """Horodatage affiché dans le Sheets : 'YYYY-MM-DD HH:MM' en heure locale."""
    return local_now().strftime("%Y-%m-%d %H:%M")


def to_local_date(dt_utc: datetime) -> str:
    """Convertit un datetime (UTC ou aware) en date locale 'YYYY-MM-DD'."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    if _LOCAL_TZ is not None:
        return dt_utc.astimezone(_LOCAL_TZ).strftime("%Y-%m-%d")
    return dt_utc.astimezone().strftime("%Y-%m-%d")
