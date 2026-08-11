<div align="center">

# Proxion

**Messaggistica privata che è davvero tua.**

Chat, voce e video con vera crittografia end-to-end, dove le tue conversazioni vivono in uno
spazio di archiviazione che controlli tu, non sui server di un'azienda. Costruito sullo standard
aperto [Solid](https://solidproject.org). Nessun numero di telefono, nessuna registrazione,
nessuna azienda nel mezzo.

**Leggi nella tua lingua:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · Italiano · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion sul desktop: una conversazione crittografata end-to-end, con stanze e contatti nella barra laterale" width="800">

</div>

## Cos'è Proxion?

Proxion è una app di messaggistica come quelle che usi già, con una differenza che cambia tutto:
i tuoi dati appartengono a te.

I tuoi messaggi, file e cronologia delle chiamate vivono nel tuo **pod Solid**, uno spazio di
archiviazione personale che controlli tu, invece di restare chiusi dentro l'app di un'azienda.
Scegli un fornitore di pod gratuito, porta il tuo o ospitalo da te, e spostati quando vuoi. La
tua identità viene creata sul tuo dispositivo, quindi non c'è nessun account da registrare e
niente che possa trapelare.

È una app di messaggistica vera, di tutti i giorni: stanze e messaggi diretti, chiamate voce e
video con condivisione dello schermo, file, reazioni, risposte e altro ancora, su Windows,
macOS, Linux e sul web.

## Ottenere Proxion

**Scaricalo e aprilo.** Non c'è niente da configurare né alcun server da avviare.

- **Windows, macOS o Linux:** vai alla [pagina di installazione](https://cafetechne.github.io/proxion-messenger/)
  o alla [versione più recente](../../releases/latest).
- **macOS con [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **Nel tuo browser:** Proxion funziona anche come app web installabile.

Poiché Proxion non è firmato da Apple né da Microsoft (di proposito, così che nessun guardiano
si frapponga tra te e il tuo software), il sistema mostra un avviso una tantum la prima volta
che lo apri. Su Windows scegli *Ulteriori informazioni e poi Esegui comunque*; su macOS *clic
destro e poi Apri*; Linux non mostra alcun avviso.

## Cosa puoi fare

- **Messaggia e chiama.** Stanze di gruppo e chat private uno a uno, oltre a chiamate voce e
  video peer-to-peer con condivisione dello schermo.
- **Conserva la tua cronologia.** Tutto vive nel tuo pod, in un formato aperto, quindi è tua da
  conservare, leggere con altri strumenti e portare con te.
- **Conversazioni davvero private.** I messaggi diretti sono crittografati end-to-end, e puoi
  confermare di parlare davvero con il tuo contatto grazie a una breve frase di sicurezza che
  leggete ad alta voce insieme. Le chiamate sono crittografate allo stesso modo.
- **Raggiungi chiunque su Solid.** Trova e invita persone in tutto l'ecosistema Solid, non solo
  altri utenti di Proxion.
- **Usalo ovunque.** Desktop, browser e cellulare, con funzionamento offline, in sei lingue tra
  cui l'arabo da destra a sinistra, e pensato per funzionare con solo screen reader e tastiera.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion in esecuzione su un telefono" width="240">
</p>

## Parte dell'ecosistema Solid

Proxion è un buon cittadino di Solid, non un giardino recintato che usa Solid soltanto sotto il
cofano. Una stanza che crei viene scritta nel formato di chat standard di Solid, così che altre
app Solid possano leggerla e unirsi.

<img src="landing/assets/interop-sidebyside.png" alt="La stessa stanza mostrata fianco a fianco in Proxion e nel databrowser di SolidOS, con gli stessi messaggi" width="900">

- **Apri una stanza di Proxion in [SolidOS](https://solidos.org)** e ogni messaggio è lì. Questo
  è verificato contro il vero SolidOS nei nostri test, non solo affermato.
- **Trova e invita persone tramite il loro WebID.** Scopri le stanze che qualcuno ospita, oppure
  lascia un invito nella sua casella Solid che qualsiasi app Solid può leggere.
- **Vedi messaggi e inviti nuovi in tempo reale,** che ti raggiungono anche a Proxion chiuso.
- **Le tue stanze sopravvivono a qualsiasi singolo server.** La struttura di una stanza vive nel
  tuo pod, quindi può essere ricostruita dal tuo pod soltanto.

Le stanze condivise sono aperte per scelta progettuale, così altre app possono leggerle; i
messaggi diretti privati sono crittografati end-to-end e leggibili di proposito solo dalle
persone che vi partecipano. Il formato dei dati completo è documentato in
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), il quadro di compatibilità app per app in
[docs/INTEROP.md](docs/INTEROP.md), e un audit requisito per requisito rispetto alla suite di
specifiche Solid in [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Privato per progettazione

- **Messaggi diretti e chiamate crittografati end-to-end,** così che nessun relay o server nel
  mezzo possa leggerli.
- **I tuoi dati nel tuo pod, in chiaro.** Sono dati documentati e standard, non un blob
  bloccato, quindi qualsiasi app che autorizzi può leggerli e puoi andartene quando vuoi.
- **Verificabile, non solo promesso.** Ogni download può essere ricondotto a questo codice
  sorgente pubblico, e migliaia di test automatici vengono eseguiti a ogni modifica.

Per i dettagli, incluso il modello di sicurezza delle chiamate, il modello di minaccia e come
verificare un download, vedi [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md) e
[docs/VERIFYING.md](docs/VERIFYING.md).

## Contribuire

Proxion è open source e i contributi sono davvero benvenuti, dalle segnalazioni di bug al
codice. Inizia da [CONTRIBUTING.md](CONTRIBUTING.md). Se arrivi dalla comunità Solid e qualcosa
non interopera come ti aspetti, è esattamente il tipo di problema di cui vogliamo sapere.

## Per sviluppatori e chi si auto-ospita

Alla maggior parte delle persone basta usare l'installer qui sopra. Per mettere le mani su
Proxion o far girare il tuo gateway sempre attivo (per esempio per puntare un telefono al tuo
desktop):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # credenziali pod facoltative; lascia vuoto per uso solo locale
python run_gateway.py
# apri http://localhost:8080
```

Costruire un installer nativo:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # impacchetta il gateway per la tua piattaforma
cd tauri-app && cargo tauri build # installer nativo
```

Eseguire i test:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Come si incastra tutto.** Il frontend (in `web/`) è servito da un piccolo **gateway** (in
`proxion-messenger-core/`) che custodisce le tue chiavi, parla con il tuo pod e si connette
direttamente ai gateway dei tuoi contatti. Sul desktop il gateway è incluso dentro l'app e parte
con essa, quindi non lo vedi mai e non installi Python. Il gateway esiste perché Solid copre
dati e identità ma non la consegna in tempo reale, la presenza o l'avvio delle chiamate, lo
stesso ruolo che un homeserver svolge per Matrix o un server SMTP per la posta elettronica.
Dettagli in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) e
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Licenza

[AGPL-3.0](LICENSE). Libero di usare, auto-ospitare, forkare e contribuire. Se fai girare un
Proxion modificato come servizio per altri, devi pubblicare le tue modifiche. Il punto è questo:
nessuno può trasformarlo di nuovo in un silo.
