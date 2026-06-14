# Alpheo — Documentation pour Claude Code

Outil de veille d'offres d'emploi automatisé.
Scan les alertes LinkedIn par email, score chaque offre avec Claude en deux passes (P1 batch + P2 individuel), enrichit les meilleures via RapidAPI, exporte dans Google Sheets.

## Architecture

```
Alpheo/
├── main.py                    # Orchestrateur — point d'entrée unique
├── profile.yaml               # Profil de l'utilisateur + critères de scoring (éditable sans toucher au code)
├── requirements.txt
├── .env                       # Clés API (ne pas committer)
├── credentials.json           # OAuth Google (ne pas committer)
├── token.json                 # Token OAuth généré automatiquement (ne pas committer)
└── src/
    ├── gmail_collector.py     # Lecture des alertes LinkedIn depuis Gmail
    ├── scorer.py              # Scoring deux passes via Claude API (Haiku)
    ├── job_api.py             # Enrichissement RapidAPI (Jobs API by Patrick) + cache Sheets
    ├── sheets_output.py       # Export Google Sheets
    └── _fantastic_jobs_api.py # Archive Fantastic.Jobs (quota épuisé, gardé pour référence)
```

## Flux de données

```
Gmail (alertes LinkedIn) → parse texte brut → JobOffer[]
  → Passe 1 (P1) : scoring batch Claude (1 appel pour toutes les offres) → score P1
  → Filtre : offres score P1 >= 4 → enrichissement RapidAPI (description complète)
  → Passe 2 (P2) : scoring individuel Claude (1 appel/offre, 5 workers) → analyse complète
  → Google Sheets (toutes les offres, rejetées incluses avec Accepté=FALSE)
```

## Commandes utiles

```powershell
# Depuis le dossier Alpheo/, toujours préfixer avec :
$env:PYTHONIOENCODING="utf-8"

# Scan standard (24h)
python main.py

# Rattrapage sur N jours
python main.py --days 60

# Fenêtre glissante : J-15 à J-8
python main.py --from-day 8 --days 7

# Test sans Gmail (4 offres fictives)
python main.py --test --no-sheets

# Sans enrichissement API (plus rapide, économise crédits)
python main.py --no-enrich

# Limiter les appels API réels (le cache est toujours utilisé)
python main.py --enrich-limit 3

# Cache uniquement, 0 appel API réel
python main.py --enrich-limit 0

# Sauvegarder aussi en JSON
python main.py --output-json results.json

# Rescorer en P2 toutes les offres sans P2 (score P1 >= 6 par défaut)
python main.py --rescore-p2

# Rescorer en P2 avec score P1 minimum personnalisé
python main.py --rescore-p2 --rescore-min-score 8

# Rescorer même les offres qui ont déjà une P2 (après correction du prompt)
python main.py --rescore-p2 --rescore-force --enrich-limit 0

# Rescorer uniquement une offre par son ID LinkedIn (col B du Sheets)
python main.py --rescore-id 4379033220
```

## Variables d'environnement (.env)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Clé API Claude (console.anthropic.com) |
| `RAPIDAPI_KEY` | Clé Jobs API by Patrick (rapidapi.com) — 50 crédits/mois |
| `GOOGLE_SPREADSHEET_ID` | ID du Sheets → `1n5dLkWlhrKI23pz9prjERm_R_wsdhEcq6SJcisSVHjk` |

## Google Sheets

URL : https://docs.google.com/spreadsheets/d/1n5dLkWlhrKI23pz9prjERm_R_wsdhEcq6SJcisSVHjk

### Onglet "Offres" — 34 colonnes

| Col | Nom | Rempli par |
|-----|-----|------------|
| A | Date ajout (P1) | Code — date d'insertion |
| B | ID LinkedIn | Code — extrait de l'URL |
| C | Date offre | Code — date de l'email |
| D | Score P1 /10 | Claude P1 |
| E | Résumé P1 | Claude P1 — raison rejet ou "GO — Score P1: X/10" |
| F | Accepté | Code — TRUE si score >= 6 et non rejeté |
| G | Date P2 | Code — date du scoring P2 |
| H | Score P2 /10 | Claude P2 |
| I | Reco P2 | Claude P2 — GO / NO GO |
| J | Rôle | Claude P2 — sous-score rôle |
| K | Score Entreprise | Claude P2 — sous-score entreprise |
| L | Lieu | Claude P2 — sous-score localisation |
| M | Score User | **Manuel** |
| N | Reco User | **Manuel** |
| O | Motif User | **Manuel** |
| P | Statut | **Manuel** — Postulé / Pas intéressé / En cours |
| Q | Comm | **Manuel** |
| R | URL | Code |
| S | Titre | Code |
| T | Entreprise | Code — nom de l'entreprise |
| U | Localisation | Code |
| V | Dutch Required? | Claude P2 — mandatory / preferred / vide |
| W | Salaire affiché | Code |
| X | Salaire estimé | Claude P2 |
| Y | Description entreprise | Claude P2 — résumé 2-3 phrases |
| Z | Résumé | Claude P2 — analyse complète |
| AA | Points forts | Claude P2 |
| AB | Red flags | Claude P2 |
| AC | Taille entreprise | Claude P2 — estimée |
| AD | Secteur | API |
| AE | Séniorité | API |
| AF | Funding / Type | Claude P2 — Série A/B, Bootstrapped, Corporate… |
| AG | Description offre | API — description complète |
| AH | Source | Code — email LinkedIn |

**Règles importantes :**
- Colonnes A-E : figées après P1, jamais écrasées par un rescore P2
- Colonnes M-Q : manuelles (`MANUAL_COLUMNS`), jamais écrasées par le code
- Dédoublonnage par URL (col R) à chaque insertion
- Les offres sont triées par score décroissant à l'insertion

### Onglet "Cache API" — 3 colonnes

| Col | Nom |
|-----|-----|
| A | enriched_at |
| B | linkedin_url |
| C | api_raw_json |

Évite les re-requêtes à la RapidAPI. Toujours consulté avant tout appel API. Un appel échoué (429, quota) n'est pas mis en cache.

## Profil de scoring (profile.yaml)

Tous les critères sont dans `profile.yaml` — **modifier ce fichier suffit** pour changer le comportement du scoring :
- **Rôles forts** : Head of Ops, Head of Product, Chief of Staff, GM, VP Ops, Director Ops, Head of IT, COO
- **Rôles acceptables** : Senior PM, Ops Manager senior, Country Manager (ops), Data/AI Lead (hands-on), Head of CS (avec équipe), Head of Account Management (scope stratégique/ops)
- **Rôles rejetés** : dev pur, data science pur, finance, RH, sales pur, IC sans équipe, junior
- **Entreprises cibles** : startup/scale-up tech 20-300 pers., SaaS, marketplace, mobilité, énergie, retail tech, IA
- **Salaire minimum** : 90k€ brut/an (hard reject si explicitement < 80k€)
- **Localisation** : Belgique uniquement (max 1h Bruxelles), remote/hybride accepté

## Points techniques importants

### Parsing des emails LinkedIn
Les emails LinkedIn sont en **texte brut** (pas HTML). Format :
```
Titre du poste
Nom entreprise
Ville
Voir l'offre d'emploi : https://www.linkedin.com/comm/jobs/view/ID/?...
```
L'ID est extrait et l'URL canonique `linkedin.com/jobs/view/ID/` est reconstituée.
3 expéditeurs traités : `jobalerts-noreply@linkedin.com`, `jobs-listings@linkedin.com`, `jobs-noreply@linkedin.com`.

### Scoring — deux passes

**Passe 1 (batch)**
- 1 seul appel Claude pour toutes les offres (batches de 50, `time.sleep(3)` entre batches)
- Input : titre | entreprise | localisation | salaire
- Output : `[{id, score, go, reason}]` — reason rempli uniquement si `go=false`
- Formule : `score = (role×5 + company×3 + location×2) / 10`

**Passe 2 (individuel)**
- 1 appel Claude par offre, 5 workers en parallèle
- Input : profil complet + description complète (non tronquée), `max_tokens=1500`
- Output JSON : `{hard_reject, score_role, score_company, score_location, score_total, recommendation, dutch_required, salary_estimate, company_size, company_funding, company_description, strengths, red_flags, summary}`
- Déclenché si : score P1 >= 4 **et** description disponible (> 150 chars)
- En cas d'erreur Claude (429, timeout) : `p2_failed=True` → ligne non écrasée dans le Sheets

**Seuils**
| Constante | Valeur | Rôle |
|-----------|--------|------|
| `PRE_ENRICHMENT_THRESHOLD` | 4 | Score P1 min pour déclencher l'enrichissement API |
| `SHORTLIST_THRESHOLD` | 6 | Score min pour `Accepté=TRUE` |

### Colonnes protégées
- `P2_START_COL = "Accepté"` : les rescores P2 n'écrivent qu'à partir de la col F
- `MANUAL_COLUMNS = {Score User, Reco User, Motif User, Statut, Comm}` : jamais écrasées, même avec `--rescore-force`
- `job_to_p2_updates()` : batchUpdate cellule par cellule pour respecter ces deux contraintes

### ScoredJob._extra
Les champs d'enrichissement (`company_size`, `company_industry`, `seniority_level`, `company_funding`, `company_description`, `recommendation`, `dutch_required`, `p2_failed`…) transitent via `_extra` et sont fusionnés dans `to_dict()`.

### Enrichissement RapidAPI
- Endpoint : `GET /v2/linkedin/get?id=JOB_ID` (jobs-api14.p.rapidapi.com)
- 50 crédits/mois — quota se renouvelle mensuellement
- Retourne : description complète, secteur, séniorité (taille/funding estimés par Claude P2)
- `jobs_to_enrich = jobs` (tous les jobs passent par la boucle) — `max_jobs` limite seulement les appels API réels, pas la lecture du cache

### Auth Google OAuth
- `token.json` est généré au premier run (ouvre un navigateur)
- Scopes requis : `gmail.readonly` + `spreadsheets`
- Si le token expire : supprimer `token.json` et relancer **en foreground** dans un terminal PowerShell direct — le navigateur ne peut pas s'ouvrir si lancé en background via Claude Code

## Problèmes connus et solutions

| Problème | Cause | Solution |
|----------|-------|----------|
| `Rate limit 429 Claude` | 5 workers P2 sur gros volume | Les lignes avec `p2_failed=True` ne sont pas écrasées — relancer `--rescore-id ID` ou `--rescore-p2` après quelques minutes |
| `Score P2 = 0, Résumé vide` | Erreur Claude lors du rescore, lignes vidées manuellement | Vider les colonnes F→AH de la ligne dans le Sheets, relancer `--rescore-id ID` |
| `429 RapidAPI` | Quota 50 appels/mois épuisé | `--enrich-limit 0` pour cache uniquement ; quota se renouvelle mensuellement |
| `WSGITimeoutError` OAuth | Lancé en background | Lancer en foreground dans un terminal PowerShell, pas via Claude Code |
| `charmap codec error` | Windows UTF-8 | Toujours préfixer avec `$env:PYTHONIOENCODING="utf-8"` |
| `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | Token créé sans scope Sheets | Supprimer `token.json`, relancer |
| Offres sans description après P1 | API quota épuisé ou offre expirée | Normal — scorées en P1 uniquement, Accepté=FALSE |

## Coûts estimés (usage quotidien ~20 offres/jour)

| Service | Coût |
|---------|------|
| Claude Haiku P1 (batch ~20 offres) | ~0.001$/jour |
| Claude Haiku P2 (~5 offres enrichies) | ~0.003$/jour |
| RapidAPI Jobs API (~5 offres/jour) | ~3$/mois (50 crédits/mois) |
| Google APIs | Gratuit |
| **Total** | **~3$/mois** |

## Améliorations futures identifiées

- [ ] **Scheduler n8n** : trigger quotidien à 8h (`python main.py`)
- [ ] **Sources supplémentaires** : Welcome to the Jungle, Indeed Belgique via RSS
- [ ] **Filtre pré-Claude** : rejeter hors Belgique et rôles évidents sans appel Claude (partiellement en place avec `is_belgium()`)
- [ ] **Notification email/Slack** : résumé des offres GO directement
- [ ] **Tier 2 Anthropic** : passer $40 de crédits pour éviter les 429 P2 sur gros volumes
- [ ] **API alternative** : voir `src/_fantastic_jobs_api.py` (Fantastic.Jobs, 200 crédits/mois) si RapidAPI épuisée
