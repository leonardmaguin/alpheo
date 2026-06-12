# Job Scanner — Guide de Setup

Scanner automatique d'offres d'emploi avec scoring Claude.

## Structure du projet

```
job-scanner/
├── main.py                  # Orchestrateur principal
├── profile.yaml             # Ton profil et critères de matching
├── requirements.txt
├── .env.example             # → copier en .env
├── credentials.json         # → à créer (Google Cloud)
└── src/
    ├── gmail_collector.py   # Lecture des alertes LinkedIn Gmail
    ├── scorer.py            # Scoring Claude API
    ├── enricher.py          # Enrichissement Firecrawl
    └── sheets_output.py     # Export Google Sheets
```

---

## Étape 1 — Installer les dépendances Python

```powershell
cd C:\Users\leona\Documents\Alphalyr\Alpheo\job-scanner
pip install -r requirements.txt
```

---

## Étape 2 — Clé API Anthropic

1. Va sur https://console.anthropic.com/settings/keys
2. Crée une nouvelle clé API
3. Copie-la dans `.env` : `ANTHROPIC_API_KEY=sk-ant-...`

---

## Étape 3 — Google Cloud (Gmail + Sheets)

### 3a. Créer un projet Google Cloud

1. Va sur https://console.cloud.google.com/
2. Crée un nouveau projet : `job-scanner`
3. Dans "APIs & Services" → "Enable APIs" → active :
   - **Gmail API**
   - **Google Sheets API**

### 3b. Créer les credentials OAuth

1. "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth client ID"
2. Type : **Desktop application**
3. Nom : `job-scanner`
4. Télécharge le JSON → renomme-le `credentials.json`
5. Place-le dans `C:\Users\leona\Documents\Alphalyr\Alpheo\job-scanner\`

### 3c. Configurer l'écran de consentement OAuth

1. "APIs & Services" → "OAuth consent screen"
2. User type : **External**
3. Ajoute ton email `leonard.maguin@gmail.com` dans "Test users"

---

## Étape 4 — Clé API Firecrawl (optionnel mais recommandé)

1. Va sur https://www.firecrawl.dev/ → crée un compte
2. Récupère ta clé API
3. Ajoute dans `.env` : `FIRECRAWL_API_KEY=fc-...`

Sans Firecrawl, le scoring fonctionne mais sans enrichissement des offres vagues.

---

## Étape 5 — Créer le fichier .env

```powershell
Copy-Item .env.example .env
# Puis édite .env avec tes clés
```

---

## Étape 6 — Premier lancement (test)

```powershell
# Test sans Gmail ni Sheets pour vérifier que Claude score correctement
python main.py --test --no-sheets

# Si ça marche, vrai lancement (ouvrira un navigateur pour autoriser Gmail)
python main.py --days 7 --no-enrich
```

Au premier run avec Gmail, un navigateur s'ouvrira pour l'autorisation OAuth.
Un fichier `token.json` sera créé — il ne faudra plus se ré-authentifier.

---

## Utilisation quotidienne

```powershell
# Scan standard des dernières 24h
python main.py

# Scan des 3 derniers jours (rattrapage)
python main.py --days 3

# Sans enrichissement (plus rapide, économise des crédits Firecrawl)
python main.py --no-enrich

# Sauvegarder aussi en JSON
python main.py --output-json results.json
```

---

## Automatisation avec n8n

Pour lancer le script automatiquement chaque matin :

1. Dans n8n, crée un nouveau workflow
2. **Trigger** : Schedule (tous les jours à 8h00)
3. **Action** : Execute Command
   ```
   python C:\Users\leona\Documents\Alphalyr\Alpheo\job-scanner\main.py
   ```

---

## Résultat : Google Sheets

Le script crée automatiquement un Google Sheets "Job Scanner" avec les colonnes :

| Score | Titre | Entreprise | Résumé | Points forts | Red flags | URL | Statut |
|-------|-------|-----------|--------|-------------|-----------|-----|--------|

**Colonne Statut** : à remplir manuellement avec `Postulé` / `Pas intéressé` / `En cours`

---

## Ajuster le profil

Edite [profile.yaml](profile.yaml) pour modifier :
- Les rôles cibles / à rejeter
- Le salaire minimum
- Les types d'entreprises
- Les red flags

Les changements sont pris en compte au prochain run sans toucher au code.

---

## Coûts estimés

| Service | Usage | Coût estimé |
|---------|-------|------------|
| Claude Haiku | ~50 offres/jour | ~0.01$/jour |
| Firecrawl | ~10 enrichissements/jour | ~0.05$/jour |
| Google APIs | Gmail + Sheets | Gratuit |

**Total : < 2$/mois** pour un usage quotidien.
