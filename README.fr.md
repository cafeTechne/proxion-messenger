<div align="center">

# Proxion

**Une messagerie privée qui vous appartient vraiment.**

Chat, voix et vidéo avec un vrai chiffrement de bout en bout, où vos conversations vivent dans
un stockage que vous contrôlez plutôt que sur les serveurs d'une entreprise. Construit sur le
standard ouvert [Solid](https://solidproject.org). Pas de numéro de téléphone, pas
d'inscription, aucune entreprise au milieu.

**Lisez ceci dans votre langue :** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · Français · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion sur ordinateur : une conversation chiffrée de bout en bout, avec les salons et les contacts dans la barre latérale" width="800">

</div>

## Qu'est-ce que Proxion ?

Proxion est une messagerie comme celles que vous utilisez déjà, avec une différence qui change
tout : vos données vous appartiennent.

Vos messages, vos fichiers et votre historique d'appels vivent dans votre propre **pod Solid**,
un espace de stockage personnel que vous contrôlez, au lieu d'être enfermés dans l'application
d'une entreprise. Choisissez un fournisseur de pod gratuit, apportez le vôtre ou hébergez-le
vous-même, et déménagez quand vous voulez. Votre identité est créée sur votre appareil, il n'y
a donc aucun compte à créer et rien qui puisse fuiter.

C'est une vraie messagerie du quotidien : salons et messages directs, appels voix et vidéo avec
partage d'écran, fichiers, réactions, réponses et plus encore, sur Windows, macOS, Linux et le
web.

## Obtenir Proxion

**Téléchargez-le et ouvrez-le.** Il n'y a rien à configurer ni aucun serveur à faire tourner.

- **Windows, macOS ou Linux :** allez sur la [page d'installation](https://cafetechne.github.io/proxion-messenger/)
  ou la [dernière version](../../releases/latest).
- **macOS avec [Homebrew](https://brew.sh) :** `brew install cafeTechne/proxion/proxion`
- **Dans votre navigateur :** Proxion fonctionne aussi comme une application web installable.

Comme Proxion n'est pas signé par Apple ni par Microsoft (volontairement, pour qu'aucun gardien
ne se place entre vous et votre propre logiciel), votre système affiche un avertissement unique
la première fois que vous l'ouvrez. Sur Windows choisissez *Informations complémentaires puis
Exécuter quand même* ; sur macOS *clic droit puis Ouvrir* ; Linux n'affiche aucun
avertissement.

## Ce que vous pouvez faire

- **Envoyer des messages et appeler.** Salons de groupe et discussions privées en tête-à-tête,
  ainsi que des appels voix et vidéo pair à pair avec partage d'écran.
- **Garder votre historique.** Tout vit dans votre pod, dans un format ouvert, il est donc à
  vous : à conserver, à lire avec d'autres outils et à emporter avec vous.
- **Des conversations vraiment privées.** Les messages directs sont chiffrés de bout en bout,
  et vous pouvez confirmer que vous parlez bien à votre contact grâce à une courte phrase de
  sécurité que vous lisez à voix haute ensemble. Les appels sont chiffrés de la même façon.
- **Joindre n'importe qui sur Solid.** Trouvez et invitez des personnes dans tout l'écosystème
  Solid, pas seulement d'autres utilisateurs de Proxion.
- **L'utiliser partout.** Ordinateur, navigateur et mobile, capable de fonctionner hors ligne,
  en six langues dont l'arabe de droite à gauche, et conçu pour fonctionner au seul lecteur
  d'écran et clavier.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion sur un téléphone" width="240">
</p>

## Membre de l'écosystème Solid

Proxion est un bon citoyen de Solid, pas un jardin clos qui se contente d'utiliser Solid en
coulisses. Un salon que vous créez est écrit au format de chat standard de Solid, si bien que
d'autres applications Solid peuvent le lire et le rejoindre.

<img src="landing/assets/interop-sidebyside.png" alt="Le même salon affiché côte à côte dans Proxion et dans le navigateur de données SolidOS, avec les mêmes messages" width="900">

- **Ouvrez un salon Proxion dans [SolidOS](https://solidos.org)** et chaque message y est.
  C'est vérifié contre le vrai SolidOS dans nos tests, pas seulement affirmé.
- **Trouvez et invitez des personnes par leur WebID.** Découvrez les salons que quelqu'un
  héberge, ou déposez une invitation dans sa boîte de réception Solid que n'importe quelle
  application Solid peut lire.
- **Voyez les nouveaux messages et invitations en temps réel,** qui vous parviennent même
  lorsque Proxion est fermé.
- **Vos salons survivent à n'importe quel serveur.** La structure d'un salon vit dans votre
  pod, elle peut donc être reconstruite à partir de votre pod seul.

Les salons partagés sont ouverts par conception pour que d'autres applications puissent les
lire ; les messages directs privés sont chiffrés de bout en bout et lisibles à dessein
uniquement par les personnes qui y participent. Le format de données complet est documenté dans
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), le tableau de compatibilité application par
application dans [docs/INTEROP.md](docs/INTEROP.md), et un audit exigence par exigence face à la
suite de spécifications Solid dans [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Privé par conception

- **Messages directs et appels chiffrés de bout en bout,** de sorte qu'aucun relais ni serveur
  au milieu ne peut les lire.
- **Vos données dans votre pod, à découvert.** Ce sont des données documentées et standard, pas
  un bloc verrouillé, donc toute application que vous autorisez peut les lire et vous pouvez
  partir quand bon vous semble.
- **Vérifiable, pas seulement promis.** Chaque téléchargement peut être retracé jusqu'à ce code
  source public, et des milliers de tests automatisés s'exécutent à chaque changement.

Pour les détails, y compris le modèle de sécurité des appels, le modèle de menace et comment
vérifier un téléchargement, voir [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md) et
[docs/VERIFYING.md](docs/VERIFYING.md).

## Contribuer

Proxion est open source et les contributions sont vraiment bienvenues, du rapport de bogue au
code. Commencez par [CONTRIBUTING.md](CONTRIBUTING.md). Si vous venez de la communauté Solid et
que quelque chose n'interopère pas comme vous l'attendez, c'est exactement le genre de problème
dont nous voulons entendre parler.

## Pour les développeurs et les auto-hébergeurs

La plupart des gens devraient simplement utiliser l'installeur ci-dessus. Pour bidouiller
Proxion ou faire tourner votre propre passerelle toujours active (par exemple pour pointer un
téléphone vers votre ordinateur) :

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # identifiants de pod facultatifs ; laissez vide pour un usage local seul
python run_gateway.py
# ouvrez http://localhost:8080
```

Construire un installeur natif :

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # empaqueter la passerelle pour votre plateforme
cd tauri-app && cargo tauri build # installeur natif
```

Lancer les tests :

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Comment tout s'articule.** Le frontend (dans `web/`) est servi par une petite **passerelle**
(dans `proxion-messenger-core/`) qui garde vos clés, parle à votre pod et se connecte
directement aux passerelles de vos contacts. Sur ordinateur la passerelle est intégrée à
l'application et démarre avec elle, vous ne la voyez donc jamais et n'installez pas Python. La
passerelle existe parce que Solid couvre les données et l'identité mais pas la livraison en
direct, la présence ni l'établissement des appels, le même rôle qu'un homeserver joue pour
Matrix ou qu'un serveur SMTP joue pour le courrier électronique. Détails dans
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) et [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Licence

[AGPL-3.0](LICENSE). Libre d'utilisation, d'auto-hébergement, de fork et de contribution. Si
vous faites tourner un Proxion modifié comme service pour d'autres, vous devez publier vos
modifications. C'est tout l'intérêt : personne ne peut le retransformer en silo.
