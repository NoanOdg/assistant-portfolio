# Contrat de l'API pour le widget du site

Ce fichier suffit pour coder le widget de noan-ouedraogo.dev. Il décrit le service tel qu'il est dans `src/assistant/api.py` (23/11/2026). Toute modification de l'API met ce fichier à jour dans le même commit.

## 1. Base et chemins

- **Base** : l'URL du service Cloud Run (`terraform output url`, de la forme `https://assistant-….europe-west1.run.app`), sans barre finale. Le widget la lit dans un attribut de données (par exemple `data-api` sur le formulaire), jamais en dur dans le script. En local : `http://localhost:8080`.
- Tous les chemins ci-dessous sont relatifs à cette base.

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/defi` | Délivre un défi ALTCHA signé |
| `POST` | `/session` | Vérifie la solution du défi, rend un jeton de session |
| `POST` | `/question` | Pose une question, réponse en flux SSE |
| `GET` | `/sante` | Sonde : `{"etat": "ok", "version": "0.1.0"}` |

Aucun cookie, aucune authentification par le navigateur : ne pas passer `credentials: "include"`. Garder le jeton de session **en mémoire** (variable du script), ni cookie, ni `localStorage`.

## 2. Origines et en-têtes (CORS)

Origines admises, et elles seules :
- `https://noan-ouedraogo.dev` (pas `www.`) ;
- les aperçus Cloudflare Pages du projet : `https://<empreinte ou branche>.<projet>.pages.dev` et `https://<projet>.pages.dev`, si `securite.projet_pages` est renseigné dans `config/reglages.toml` ;
- `http://localhost:4321` (essais locaux, `astro dev` ou `astro preview`).

Méthodes `GET` et `POST`. En-têtes de requête admis : `Content-Type` et `Authorization`. Le navigateur envoie une requête préalable `OPTIONS` pour `/session` et `/question` (corps JSON, en-tête `Authorization`) ; sa réponse est gardée 600 s.

Côté site : la politique de sécurité (`public/_headers` et Caddyfile) doit autoriser l'origine du service dans `connect-src`. `script-src` reste `'self'` (décision 05) : le script du widget, et celui d'ALTCHA s'il est utilisé, sont servis par le site. `Cross-Origin-Embedder-Policy: require-corp` ne gêne pas : les appels `fetch` en mode `cors` sont permis.

## 3. Défi ALTCHA : `GET /defi`

Réponse `200`, `Content-Type: application/json`, `Cache-Control: no-store` :

```json
{
  "parameters": {
    "algorithm": "PBKDF2/SHA-256",
    "cost": 5000,
    "keyLength": 32,
    "keyPrefix": "00",
    "nonce": "28167abd0345457f4a7568b167544bf2",
    "salt": "2e0a0bd3c17195b5800d74e720a345f1",
    "expiresAt": 1795474382
  },
  "signature": "329f41e93dcbf6f55c5159b93721d4e58c5b8ed5b3e9bd1a6b4cf3e15c08b1ff"
}
```

C'est le format « PoW v2 » d'ALTCHA (bibliothèque Python `altcha` 2.5, widget `altcha` 3.x). Les valeurs viennent de `[altcha]` dans `config/reglages.toml` : ne pas les coder en dur, toujours lire celles du défi.

- `nonce`, `salt` : 16 octets en hexadécimal, neufs à chaque défi.
- `expiresAt` : horodatage Unix en secondes. **Un défi vaut 120 s** : le demander juste avant de le résoudre, pas au chargement de la page.
- `signature` : HMAC-SHA-256 des paramètres. Le moindre changement d'un paramètre invalide le défi.

### Résoudre

Trouver le plus petit entier `counter` (0, 1, 2…, au plus 2³² − 1) tel que :

```
password   = octets(nonce en hexa) || counter en uint32 grand-boutiste (4 octets)
derivedKey = PBKDF2-HMAC-SHA-256(password, sel = octets(salt en hexa), itérations = cost, longueur = keyLength octets)
hex(derivedKey) commence par keyPrefix
```

Avec `keyPrefix = "00"`, il faut 256 essais en moyenne. Le calcul se fait dans un Web Worker (fichier servi par le site, `worker-src` retombe sur `script-src 'self'`) pour ne pas figer la page. Prévoir une borne (par exemple 30 s) et un message si elle est atteinte.

Esquisse avec WebCrypto, sans bibliothèque :

```js
const hex = (b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
const octets = (h) => Uint8Array.from(h.match(/../g), (x) => parseInt(x, 16));

async function resoudre(defi) {
  const p = defi.parameters;
  const nonce = octets(p.nonce), salt = octets(p.salt);
  const mdp = new Uint8Array(nonce.length + 4);
  mdp.set(nonce);
  const vue = new DataView(mdp.buffer);
  for (let counter = 0; counter <= 0xffffffff; counter++) {
    vue.setUint32(nonce.length, counter, false); // grand-boutiste
    const cle = await crypto.subtle.importKey("raw", mdp, "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt, iterations: p.cost }, cle, p.keyLength * 8);
    const derivedKey = hex(bits);
    if (derivedKey.startsWith(p.keyPrefix)) return { counter, derivedKey };
  }
  return null;
}
```

Autre voie : le widget officiel `altcha` 3.x (licence MIT), copié dans le site avec la variante `altcha/external` et le worker `altcha/workers/pbkdf2` servis par le site (la variante par défaut crée ses workers depuis une URL `blob:`, refusée par la politique de sécurité). Il produit la même charge.

### Charge à envoyer

```js
const charge = btoa(JSON.stringify({ challenge: defi, solution: { counter, derivedKey } }));
```

- `challenge` : l'objet reçu de `/defi`, **tel quel** (paramètres et signature).
- `solution.counter` : entier ; `solution.derivedKey` : clé dérivée en hexadécimal minuscule (64 caractères). Un champ `time` (millisecondes) est toléré.
- Base64 standard (`btoa`), sans retour à la ligne. Environ 550 caractères ; le service refuse au-delà de 4 096.

## 4. Session : `POST /session`

Requête : `Content-Type: application/json`, corps `{"altcha": "<charge>", "precedente": "<ancien jeton>"}`. `precedente` est facultatif (1 024 caractères au plus) : au renouvellement, le widget y met le jeton qu'il remplace, même expiré.

| Code | Corps | Que faire |
|---|---|---|
| `200` | `{"session": "<jeton>", "expire_dans": 1800, "reprise": true}` | Garder le jeton en mémoire ; il vaut `expire_dans` secondes (30 min). `reprise` : la conversation de l'ancien jeton continue (§ 7 bis) ; `false` : conversation neuve. |
| `403` | `{"detail": "verification anti-robots refusee", "motif": "invalide" \| "expire" \| "rejoue"}` | Redemander un défi, le résoudre, renvoyer. **Une seule nouvelle tentative**, puis message d'erreur. |
| `422` | `{"detail": [...]}` (validation FastAPI) | Bogue du widget : champ `altcha` absent, vide ou trop long. |

Motifs du `403` :
- `invalide` : solution fausse, défi modifié, signé par une autre clé, ou charge illisible ;
- `expire` : défi de plus de 120 s ;
- `rejoue` : ce défi a déjà été présenté. **Chaque défi ne sert qu'une fois**, que la solution soit juste ou fausse.

Renouvellement glissant : `reprise` vaut `true` si l'ancien jeton est signé par le service, expiré depuis moins de 30 min, et si sa conversation a reçu une question depuis moins de 30 min. Sinon, ou sans `precedente`, `false`, sans erreur : un jeton illisible ne bloque jamais la session. Le défi ALTCHA reste exigé à chaque renouvellement.

## 5. Question : `POST /question`

Requête :
- en-têtes `Content-Type: application/json` et `Authorization: Bearer <jeton de session>` ;
- corps `{"question": "...", "langue": "fr"}`.

| Champ | Règle |
|---|---|
| `question` | chaîne de 1 à 500 caractères (compter côté widget : `maxlength="500"`) |
| `langue` | `"fr"` ou `"en"`, la langue de la page ; `"fr"` si absent. Elle choisit la langue des textes écrits d'avance et des passages cherchés. |

Réponses :

| Code | Type | Corps | Que faire |
|---|---|---|---|
| `200` | `text/event-stream` | flux SSE (§ 6) | Lire le flux jusqu'à `fin`. |
| `401` | JSON | `{"detail": "session absente ou expiree"}` | Rouvrir une session (§ 3 et 4), puis renvoyer la question **une fois**. |
| `422` | JSON | `{"detail": [...]}` | Question vide, trop longue, ou langue inconnue. |
| `429` | JSON | `{"texte": "..."}` | Afficher `texte` tel quel (déjà dans la langue demandée) : limite du visiteur (5 par minute, 30 par jour) ou plafond du site (60 par jour). Ne pas réessayer seul. |
| `500`, `502`, `503`, `504`, coupure réseau | quelconque | | Message générique de la langue de la page (« L'assistant n'a pas pu répondre. Réessayez dans un instant. » / « The assistant couldn't answer. Please try again shortly. »). |

`EventSource` ne sait pas faire de `POST` : lire le flux avec `fetch` et `response.body.getReader()`, décodé en UTF-8 (`TextDecoder`, option `stream: true`).

## 6. Flux SSE

Chaque événement :

```
event: <type>
data: <JSON sur une seule ligne>

```

Les événements sont séparés par une ligne vide (`\n\n`). `data` est toujours un objet JSON sur une ligne (les retours à la ligne du texte y sont échappés). Un morceau réseau peut contenir plusieurs événements ou un événement coupé : accumuler, découper sur `\n\n`, garder le reste.

| `event` | `data` | Rendu |
|---|---|---|
| `conversation` | `{"neuve": true}` | Premier événement quand le service n'a aucun échange pour cette conversation : première question, ou mémoire perdue (instance arrêtée faute de trafic, redémarrage). Si le fil montrait déjà un échange, afficher le séparateur « Nouvelle conversation, contexte réinitialisé ». Absent si la mémoire est coupée. |
| `etape` | `{"texte": "Recherche dans le portfolio"}` | État en cours, remplacé par le suivant. Textes possibles : « Recherche dans le portfolio », « Rédaction de la réponse », « Lecture de la page : <titre> », « Liste des projets » (en anglais : « Searching the portfolio », « Writing the answer », « Reading the page: <title> », « Listing the projects »). |
| `texte` | `{"texte": "morceau"}` | Morceau de la réponse, **à ajouter** au précédent. Texte brut : l'insérer avec `textContent`, jamais `innerHTML`. La réponse rédigée finit par les numéros des passages utilisés, par exemple `[1] [3]`, qui renvoient à `sources[].n`. |
| `sources` | `{"citees": true, "sources": [{"n": 1, "titre": "...", "section": "...", "url": "https://noan-ouedraogo.dev/..."}]}` | Liste de liens (5 au plus) sous la réponse. `citees: false` : le modèle n'a rien cité, ce sont les pages consultées (3 au plus) ; l'intituler « Pages consultées » plutôt que « Sources ». `url` est absolue, ancre comprise. |
| `carte` | `{"courriel": "...", "linkedin": "https://...", "cv": "https://.../CV_....pdf", "contact": "https://noan-ouedraogo.dev/contact"}` | Carte de contact rendue par le widget avec ces quatre liens (`mailto:` pour le courriel). `cv` et `contact` sont déjà dans la langue demandée. Suivie d'un `texte` d'une phrase. |
| `refus` | `{"motif": "personnel" \| "hors_corpus" \| "modele", "texte": "..."}` | Afficher `texte` à la place d'une réponse (écrit d'avance, avec renvoi vers la page Contact). |
| `erreur` | `{"texte": "..."}` | Afficher `texte`. Un texte partiel déjà reçu peut rester affiché, marqué comme interrompu. |
| `fin` | `{"trace": "<32 hexa>"}` | **Toujours le dernier**, et rien d'autre que `trace`. Fin de `aria-busy`, annonce de la réponse complète dans la zone `aria-live`. |
| `diagnostic` | | Jamais envoyé au widget (réservé à l'évaluation de la CI). Ignorer tout type inconnu. |

`trace` est l'identifiant de la question dans le journal du service : il peut être montré en petit dans un message d'erreur, pour un signalement. Le flux public ne porte ni chemin ni statistiques (coût, jetons, durées, distance du meilleur passage) : elles aideraient à sonder le seuil de refus. Elles restent dans le journal et dans le flux de l'évaluation de la CI, qui se présente avec le jeton d'identité Google du compte de déploiement (`X-Jeton-Evaluation`, jamais utilisé par le widget). Le widget déduit ce qu'il affiche des autres événements (`refus`, `erreur`, `carte`), jamais de `fin`.

Suites possibles :

| Cas | Événements |
|---|---|
| Tout flux | précédé de `conversation` si la conversation est neuve (§ 7 bis) |
| Réponse | `etape`, `etape`, `texte` × n, `sources`, `fin` (la branche agent peut ajouter des `etape` « Lecture de la page » et, rarement, une `carte` au milieu des `texte`) |
| Demande de contact | `carte`, `texte`, `fin` |
| Question personnelle | `refus` (`personnel`), `fin` |
| Rien de pertinent dans le portfolio | `etape`, `refus` (`hors_corpus`), `fin` |
| Le modèle refuse | `etape`, `etape`, `refus` (`modele`), `fin` |
| Délai de 30 s dépassé | `etape`, `etape`, `texte` × n éventuels, `erreur`, `fin` |
| Panne en cours de flux | événements déjà reçus, `erreur`, `fin` |

Si le flux se ferme sans `fin` (coupure, délai de Cloud Run de 35 s), traiter comme une erreur.

## 7. Déroulé conseillé

1. Premier focus du champ : `GET /defi`, puis résolution dans le worker. La requête réveille aussi le service (zéro instance au repos : le premier appel peut prendre quelques secondes).
2. Dès la solution trouvée : `POST /session`. Garder le jeton et l'heure d'expiration (`expire_dans`).
3. Envoi : `POST /question`. Si le jeton expire dans moins d'une minute, rouvrir d'abord une session.
4. `401` → étapes 1 et 2, puis une seule relance. `403` sur `/session` → nouveau défi, une seule relance.
5. Pendant le flux : bouton d'envoi désactivé, `aria-busy="true"` ; une seule question à la fois (les tours d'une conversation se suivent).
6. Renouvellement (jeton à moins d'une minute de l'expiration, ou `401`) : envoyer l'ancien jeton dans `precedente`. Si `reprise` vaut `false` alors qu'un échange a déjà eu lieu avec l'ancien jeton, afficher le séparateur « Nouvelle conversation, contexte réinitialisé » / « New conversation, context reset » avant la question.
7. « Nouvelle conversation » (bouton de l'en-tête, visible dès le premier échange) : vider le fil et oublier le jeton, sans `precedente` à la session suivante.

## 7 bis. Mémoire de conversation

Le service suit le fil d'une conversation : « Et quelles technos ? » après une question sur l'INSEI vise l'assistant de l'INSEI.

- **Premier tour** : rien ne change, aucun appel de plus.
- **Tours suivants** : un appel court au modèle réécrit la question en question autonome. Le code vérifie qu'elle n'ajoute aucun nom ni chiffre absent de la conversation, sinon il garde la question d'origine. En cas d'erreur ou de délai de la réécriture (4 s), la question d'origine part telle quelle : le widget ne voit aucune différence. Une demande de traduire, raccourcir ou reformuler la réponse précédente se rédige à partir des mêmes passages, sans nouvelle recherche. Le modèle qui rédige ne reçoit jamais l'historique.
- **Ce qui est gardé** : les 3 derniers échanges (question réécrite, réponse affichée tronquée à 300 caractères, identifiants des passages cités, marque de refus) et le sujet courant (dernière question réécrite qui a reçu une réponse, et les projets de ses sources). Un refus ne change pas le sujet.
- **Où, combien de temps** : en mémoire du service seulement, jamais sur disque, en base ni dans les journaux. La conversation est effacée 30 min après sa dernière question, ou au redémarrage du service. « Nouvelle conversation » dans le widget l'abandonne : sans le jeton, elle n'est plus jamais lue, et s'efface à son tour 30 min après.
- **Clé** : l'identifiant de conversation porté par le jeton de session, jamais l'adresse du visiteur.
- Aucun nombre maximal de questions par conversation : la fenêtre de 3 échanges borne le contexte. Les limites par visiteur et du site (§ 8) s'appliquent comme avant.
- L'évaluation de la CI nomme ses conversations par l'en-tête `X-Conversation` (64 caractères `[A-Za-z0-9_-]`), lu seulement avec `X-Jeton-Evaluation`. Le widget ne l'envoie jamais (il n'est pas dans les en-têtes CORS permis).

## 8. Limites du service

| Limite | Valeur |
|---|---|
| Question | 500 caractères |
| Réponse | 450 jetons, 30 s |
| Visiteur | 5 questions par minute, 30 par jour |
| Site entier | 60 questions par jour |
| Session | 30 min, renouvelable sans perdre la conversation |
| Conversation | 3 derniers échanges et le sujet courant, effacés 30 min après la dernière question |
| Défi ALTCHA | 120 s, une seule utilisation |

Toutes viennent de `config/reglages.toml` et peuvent changer : le widget n'en code aucune en dur, sauf `maxlength="500"`.

## 9. Essai local

```sh
uv run assistant-ingerer --source mdx --contenu ../Carriere/portfolio/src/content
uv run uvicorn assistant.api:app_depuis_env --factory --port 8080 --no-access-log
```

ALTCHA est actif en local avec une clé tirée au démarrage (aucun secret à poser). Le site en `astro dev` ou `astro preview` (port 4321) appelle `http://localhost:8080`.
