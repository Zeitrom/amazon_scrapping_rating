# availability_grade_async.py — v14

> **Auteur :** Zeitrom *(Hugo Verdier · @huve)*  
> **Dernière mise à jour :** 24 juin 2025  
> **Licence :** MIT

---

## ✨ Synopsis
Script Python **asynchrone** de scraping Amazon qui :

* récupère, pour chaque ASIN, **la disponibilité, la note moyenne et le nombre d’avis** ;
* gère les cas de « **No featured offers available** » (bloc *fodcx*) ;
* s’adapte aux **variations de mise en page** et aux **langues** (FR/EN/IT/DE/ES…) ;
* lit un fichier **CSV ou Excel** contenant les URLs en entrée ;
* lance du **scraping par lots** (chunks) avec **Playwright Chromium** (headless) ;
* contourne les blocages (bannière cookies, *Click‑Verify*, CAPTCHA **OCR**) ;
* applique **playwright‑stealth** et **rotation d’User‑Agent** ;
* consigne des **logs clairs** (progression & erreurs critiques) dans `scraping.log`.

---

## ⚙️ Pré‑requis

| Outil / Lib         | Version conseillée |
|---------------------|--------------------|
| Python              | ≥ 3.10            |
| Playwright          | ≥ 1.44            |
| playwright‑stealth  | ≥ 1.2             |
| pandas              | ≥ 2.2             |
| beautifulsoup4      | ≥ 4.12            |
| amazon‑captcha      | ≥ 0.7             |
| Tesseract‑OCR¹      | 5.x (facultatif)  |

> ¹ Tesseract est recommandé pour améliorer la reconnaissance des CAPTCHAs. Le script peut toutefois fonctionner sans, mais avec moins de réussite.

---

## 🚀 Installation rapide
```bash
# 1) Cloner ou télécharger le dépôt
$ git clone https://github.com/votre-compte/amazon-availability-scraper.git
$ cd amazon-availability-scraper

# 2) Créer un environnement virtuel (optionnel mais conseillé)
$ python -m venv .venv && source .venv/bin/activate  # Linux/macOS
# ou
$ py -m venv .venv && .venv\Scripts\activate      # Windows

# 3) Installer les dépendances Python
$ pip install -r requirements.txt

# 4) Installer les navigateurs Playwright
$ playwright install chromium
```

---

## 🗂️ Structure minimale du projet
```
.
├── availability_grade_async.py  # <— Script principal
├── requirements.txt            # Dépendances Python
└── README.md                   # Vous êtes ici
```

---

## 📄 Fichier d’entrée

Le script attend un fichier **Excel (.xlsx/.xls) ou CSV** contenant **une colonne** nommée `Details Page Link` qui liste les URLs produits Amazon.

Exemple :
```
ASIN,Details Page Link
B0CXYZ42XG,https://www.amazon.it/dp/B0CXYZ42XG
B0CXYZ427F,https://www.amazon.it/dp/B0CXYZ427F
```

---

## 🔧 Configuration interne
Les principaux paramètres se trouvent en tête de fichier :
| Constante               | Rôle | Valeur par défaut |
|-------------------------|------|-------------------|
| `CHUNK_SIZE`            | Nb d’URLs par lot                         | 100 |
| `MAX_CONCURRENCY`       | Nb de pages Playwright ouvertes en //     | 5 |
| `NAV_TIMEOUT`           | Timeout navigation (ms)                   | 45 000 |
| `MAX_RETRIES`           | Nb max de tentatives par URL              | 3 |
| `WAIT_BETWEEN_CHUNKS`   | Pause (s) aléatoire entre lots            | (10, 20) |
| `WAIT_BETWEEN_REQUESTS` | Pause (s) aléatoire entre URL             | (1, 3) |
| `OUTPUT_FILE`           | Nom du CSV de sortie                      | `polti_IT_asin_graded.csv` |
| **Variables env.**      |                                         | |
| `INPUT_FILE`            | Remplace le chemin par défaut de l’entrée | — |
| `SAMPLE_SIZE`           | Taille d’échantillon (débug)             | 0 (désactivé) |

---

## ▶️ Exécution
### Mode « standard »
```bash
$ python availability_grade_async.py \
       --input-file chemin/vers/mon_fichier.xlsx
```
(Sans `--input-file`, le script utilise le chemin codé dans `default_path`.)

### Mode « échantillon » (débogage rapide)
```bash
$ export SAMPLE_SIZE=50  # ou sous Windows : set SAMPLE_SIZE=50
$ python availability_grade_async.py
```
Le script tirera alors un échantillon aléatoire de 50 lignes afin de réduire le temps de test.

---

## 📥 Sortie attendue
* **CSV** : `polti_IT_asin_graded.csv` (ou nom défini dans `OUTPUT_FILE`)
* **Log** : `scraping.log`

Le CSV contient les colonnes d’origine **+** :
| availability | average_stars | total_reviews |
|--------------|--------------|---------------|
| En stock     | 4.3          | 152           |
| N/D          | "Produit avec aucune note" | "Produit avec aucune note" |

---

## 🔄 Fonctionnement général
1. **Lecture** du fichier d’entrée & découpe en *chunks* (`CHUNK_SIZE`).
2. **Lancement** d’un navigateur Chromium **headless**.
3. Pour chaque URL :
   * Consentement cookies → acceptation.
   * Détection **blocages** : bannière Click‑Verify → clic ; CAPTCHA → OCR Tesseract.
   * Extraction **disponibilité, notes, avis** via *BeautifulSoup*.
   * Gestion des échecs avec **retry** exponentiel (`MAX_RETRIES`).
4. Après chaque lot : **pause aléatoire** (anti‑bot).
5. **Fusion** des chunks puis **export** CSV.

---

## 🩺 Dépannage
| Problème                                       | Piste de résolution |
|------------------------------------------------|---------------------|
| CAPTCHA non résolu                            | Vérifier Tesseract ; ajuster qualité réseau |
| Blocage "Robot Check" persistant              | Réduire `MAX_CONCURRENCY` et/ou jouer sur `WAIT_BETWEEN_REQUESTS` |
| Bannière cookies non détectée                 | Ajouter un sélecteur au regex dans `handle_cookie_consent` |
| Timeout navigation                            | Augmenter `NAV_TIMEOUT`, connexion VPN plus rapide |

---

## ✅ Bonnes pratiques pour maximiser la réussite
* **Adresse IP propre** (pas de data‑center public, éviter les proxies déjà blacklistés).
* **Concurrence modérée** ; Amazon durcit vite la surveillance au‑delà de 5‑8 pages simultanées.
* **Rotation d’UA** & Pauses aléatoires → rester proche d’un comportement humain.
* **Logs** : surveiller `scraping.log` pour affiner les timings et détecter les motifs d’échec.

---

## 🖋️ Auteur & Contact
Créé et maintenu par **Zeitrom** (Hugo Verdier – *Analytics Engineer*).  
> Twitter / X : [@huve](https://twitter.com/huve)  
> Email : hugo.verdier@example.com

Contributions, issues & PR bienvenus !

---

## 📜 Licence
Ce projet est distribué sous licence **MIT**. Voir le fichier `LICENSE` pour les détails.
