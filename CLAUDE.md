# Job Scanner — Documentation pour Claude Code

Outil de veille d'offres d'emploi automatisé pour Léonard Maguin.
Scan les alertes LinkedIn par email, score chaque offre avec Claude, enrichit les meilleures via Firecrawl, exporte dans Google Sheets.

## Architecture

```
job-scanner/
├── main.py                  # Orchestrateur — point d'entrée unique
├── profile.yaml             # Profil de Léonard + critères de scoring (éditable sans toucher au code)
├── requirements.txt
├── .env                     # Clés API (ne pas committer)
├── credentials.json         # OAuth Google (ne pas committer)
├── token.json               # Token OAuth généré automatiquement (ne pas committer)
└── src/
    ├── gmail_collector.py   # Lecture des alertes LinkedIn depuis Gmail
    ├── scorer.py            # Scoring parallèle via Claude API (Haiku)
    ├── enricher.py          # Enrichissement Firecrawl pour les offres score >= 6
    └── sheets_output.py     # Export Google Sheets (toutes les offres, Accepté TRUE/FALSE)
```

## Flux de données

```
Gmail (alertes LinkedIn) → parse texte brut → JobOffer[]
  → scoring parallèle Claude Haiku (5 workers) → ScoredJob[]
  → enrichissement Firecrawl (si score >= 6 et FIRECRAWL_API_KEY défini)
  → Google Sheets (toutes les offres, rejetées incluses avec Accepté=FALSE)
```

## Commandes utiles

```powershell
# Depuis le dossier job-scanner/, toujours préfixer avec :
$env:PYTHONIOENCODING="utf-8"

# Scan standard
python main.py

# Rattrapage sur N jours
python main.py --days 7

# Test sans Gmail (3 offres fictives)
python main.py --test --no-sheets

# Sans enrichissement Firecrawl (plus rapide, économise crédits)
python main.py --no-enrich

# Sauvegarder aussi en JSON
python main.py --output-json results.json
```

## Variables d'environnement (.env)

| Variable | Description | Statut |
|----------|-------------|--------|
| `ANTHROPIC_API_KEY` | Clé API Claude (console.anthropic.com) | Configuré |
| `FIRECRAWL_API_KEY` | Clé Firecrawl pour scraper les pages offres | Configuré |
| `GOOGLE_SPREADSHEET_ID` | ID du Sheets existant (évite d'en recréer un) | Configuré → `1n5dLkWlhrKI23pz9prjERm_R_wsdhEcq6SJcisSVHjk` |

## Google Sheets résultat

URL : https://docs.google.com/spreadsheets/d/1n5dLkWlhrKI23pz9prjERm_R_wsdhEcq6SJcisSVHjk

Colonnes : Date ajout | **Accepté** | Score /10 | Score Rôle | Score Entreprise | Score Localisation | Titre | Entreprise | Localisation | Salaire | Résumé/Raison rejet | Points forts | Red flags | Taille entreprise | Funding | Description entreprise | URL | Source | **Statut** (à remplir manuellement)

- `Accepté = TRUE` : score >= 6 et non rejeté → offres à regarder en priorité
- `Accepté = FALSE` : rejeté (localisation, rôle hors profil, description vide, etc.)
- `Statut` : colonne manuelle — y mettre `Postulé`, `Pas intéressé`, `En cours`

## Profil de scoring (profile.yaml)

Tous les critères sont dans `profile.yaml` — **modifier ce fichier suffit** pour changer le comportement du scoring sans toucher au code :
- Rôles cibles / à rejeter
- Types d'entreprises cibles
- Salaire minimum (actuellement 90k€)
- Localisation (Belgique, max 1h Bruxelles)
- Red flags

## Points techniques importants

### Parsing des emails LinkedIn
Les emails LinkedIn alertes sont en **texte brut** (pas HTML). Format :
```
Titre du poste
Nom entreprise
Ville
Voir l'offre d'emploi : https://www.linkedin.com/comm/jobs/view/ID/?...
```
Le parser extrait l'ID LinkedIn et reconstruit l'URL canonique `linkedin.com/jobs/view/ID/`.

### Scoring
- Modèle : `claude-haiku-4-5-20251001` (rapide, économique)
- 5 workers en parallèle (limite du tier Anthropic : 50 req/min)
- Retourne **toutes** les offres (rejetées incluses) pour le Sheets
- Hard reject si : hors Belgique sans remote, rôle dev/finance/RH/junior, salaire < 80k€

### Enrichissement (Firecrawl)
- Déclenché uniquement si score >= 6 **et** `FIRECRAWL_API_KEY` défini
- Scrape la page LinkedIn de l'offre → extrait description complète + site entreprise
- Scrape le site entreprise (/about) → taille, funding, description

### Auth Google OAuth
- Le `token.json` est généré au premier run (ouvre un navigateur)
- Scopes requis : `gmail.readonly` + `spreadsheets`
- Si le token expire ou est invalide, supprimer `token.json` et relancer **en foreground** (pas en background — le navigateur ne peut pas s'ouvrir)
- Commande foreground obligatoire pour la première auth : lancer directement dans un terminal PowerShell, pas via Claude Code

## Problèmes connus et solutions

| Problème | Cause | Solution |
|----------|-------|----------|
| `0 offres shortlistées` | Descriptions vides dans les emails | Activer Firecrawl (`--no-enrich` désactivé) |
| `WSGITimeoutError` OAuth | Lancé en background | Lancer en foreground dans un terminal |
| `Rate limit 429` | Trop de workers parallèles | Réduire `max_workers` dans `scorer.py` (actuellement 5) |
| `charmap codec error` | Windows UTF-8 | Toujours préfixer avec `$env:PYTHONIOENCODING="utf-8"` |
| `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | Token créé sans scope Sheets | Supprimer `token.json`, relancer |

## Améliorations futures identifiées

- [ ] **Re-scorer les rejetées après enrichissement** : actuellement les offres rejetées pour "description vide" ne sont pas enrichies avant rejet — les rescorer après Firecrawl pourrait en débloquer certaines (ex: Ypto, BOI, Phinest)
- [ ] **Scheduler n8n** : trigger quotidien à 8h (commande : `python main.py`)
- [ ] **Sources supplémentaires** : Welcome to the Jungle, Indeed Belgique, APEC via RSS
- [ ] **Filtre pré-Claude** : rejeter hors Belgique et rôles évidents (dev, RH) sans appeler Claude — économiserait ~50% des appels API
- [ ] **Notification email/Slack** : envoyer un résumé des offres Accepté=TRUE directement
- [ ] **Re-run incrémental** : ne re-scorer que les nouvelles offres (déjà partiellement géré par le dédoublonnage URL dans Sheets)

## Coûts estimés (usage quotidien ~20 offres/jour)

| Service | Coût |
|---------|------|
| Claude Haiku (scoring) | ~0.002$/jour |
| Firecrawl (enrichissement ~5 offres) | ~0.025$/jour |
| Google APIs | Gratuit |
| **Total** | **< 1$/mois** |
