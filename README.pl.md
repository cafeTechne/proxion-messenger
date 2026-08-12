<div align="center">

# Proxion

**Prywatne wiadomości, które naprawdę należą do Ciebie.**

Czat, głos i wideo z prawdziwym szyfrowaniem end-to-end, w którym Twoje rozmowy żyją w pamięci
kontrolowanej przez Ciebie, a nie na serwerach firmy. Zbudowane na otwartym standardzie
[Solid](https://solidproject.org). Bez numeru telefonu, bez rejestracji, bez żadnej firmy
pośrodku.

**Przeczytaj w swoim języku:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · Polski · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion na komputerze: rozmowa szyfrowana end-to-end, z pokojami i kontaktami na pasku bocznym" width="800">

</div>

## Czym jest Proxion?

Proxion to komunikator podobny do tych, których już używasz, z jedną różnicą, która zmienia
wszystko: Twoje dane należą do Ciebie.

Twoje wiadomości, pliki i historia połączeń żyją w Twoim własnym **podzie Solid**, osobistej
przestrzeni pamięci, którą kontrolujesz, zamiast być zamknięte w aplikacji firmy. Wybierz
darmowego dostawcę poda, przynieś własny albo hostuj go samodzielnie, i przenieś się, kiedy
chcesz. Twoja tożsamość jest tworzona na Twoim urządzeniu, więc nie ma konta do zakładania ani
niczego, co mogłoby wyciec.

To prawdziwy, codzienny komunikator: pokoje i wiadomości bezpośrednie, połączenia głosowe i
wideo z udostępnianiem ekranu, pliki, reakcje, odpowiedzi i więcej, na Windows, macOS, Linux
oraz w sieci.

## Pobierz Proxion

**Pobierz i otwórz.** Nie ma nic do konfigurowania ani serwera do uruchamiania.

- **Windows, macOS lub Linux:** przejdź na [stronę instalacji](https://cafetechne.github.io/proxion-messenger/)
  lub do [najnowszego wydania](../../releases/latest).
- **macOS z [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **W przeglądarce:** Proxion działa też jako instalowalna aplikacja internetowa.

Ponieważ Proxion nie jest podpisany przez Apple ani Microsoft (celowo, aby żaden strażnik nie
stał między Tobą a Twoim własnym oprogramowaniem), system pokazuje jednorazowe ostrzeżenie przy
pierwszym otwarciu. W Windows wybierz *Więcej informacji, a potem Uruchom mimo to*; w macOS
*kliknij prawym przyciskiem, a potem Otwórz*; Linux nie pokazuje żadnego ostrzeżenia.

## Co możesz robić

- **Pisz i dzwoń.** Pokoje grupowe i prywatne czaty jeden na jeden, a także połączenia głosowe i
  wideo peer-to-peer z udostępnianiem ekranu.
- **Zachowaj swoją historię.** Wszystko żyje w Twoim podzie, w otwartym formacie, więc jest
  Twoje: możesz je przechowywać, czytać innymi narzędziami i zabrać ze sobą.
- **Naprawdę prywatne rozmowy.** Wiadomości bezpośrednie są szyfrowane end-to-end, a to, że
  rozmawiasz naprawdę ze swoim kontaktem, możesz potwierdzić krótką frazą bezpieczeństwa, którą
  odczytujecie na głos razem. Połączenia są szyfrowane tak samo.
- **Dotrzyj do każdego w Solid.** Znajduj i zapraszaj osoby z całego ekosystemu Solid, nie tylko
  innych użytkowników Proxion.
- **Używaj wszędzie.** Komputer, przeglądarka i telefon, z działaniem offline, w 16
  językach, w tym arabskim pisanym od prawej do lewej, i zbudowany tak, aby działać przy użyciu
  samego czytnika ekranu i klawiatury.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion działający na telefonie" width="240">
</p>

## Część ekosystemu Solid

Proxion jest dobrym obywatelem Solid, a nie otoczonym murem ogrodem, który jedynie używa Solid
pod spodem. Pokój, który tworzysz, jest zapisywany w standardowym formacie czatu Solid, więc
inne aplikacje Solid mogą go czytać i do niego dołączać.

<img src="landing/assets/interop-sidebyside.png" alt="Ten sam pokój pokazany obok siebie w Proxion i w przeglądarce danych SolidOS, z tymi samymi wiadomościami" width="900">

- **Otwórz pokój Proxion w [SolidOS](https://solidos.org)** i każda wiadomość tam jest. Jest to
  sprawdzane wobec prawdziwego SolidOS w naszych testach, a nie tylko deklarowane.
- **Znajduj i zapraszaj osoby po ich WebID.** Odkrywaj pokoje, które ktoś hostuje, albo zostaw
  zaproszenie w jego skrzynce Solid, które może odczytać dowolna aplikacja Solid.
- **Zobacz nowe wiadomości i zaproszenia na bieżąco,** docierają do Ciebie nawet przy zamkniętym
  Proxion.
- **Twoje pokoje przetrwają każdy pojedynczy serwer.** Struktura pokoju żyje w Twoim podzie, więc
  można ją odtworzyć z samego Twojego poda.

Pokoje współdzielone są z założenia otwarte, aby inne aplikacje mogły je czytać; prywatne
wiadomości bezpośrednie są szyfrowane end-to-end i celowo czytelne tylko dla osób w nich
uczestniczących. Pełny format danych jest udokumentowany w
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), obraz zgodności aplikacja po aplikacji w
[docs/INTEROP.md](docs/INTEROP.md), a audyt wymaganie po wymaganiu względem zestawu specyfikacji
Solid w [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Prywatny z założenia

- **Wiadomości bezpośrednie i połączenia szyfrowane end-to-end,** tak że żaden przekaźnik ani
  serwer pośrodku nie może ich odczytać.
- **Twoje dane w Twoim podzie, na widoku.** To udokumentowane, standardowe dane, a nie zamknięty
  blok, więc każda aplikacja, na którą zezwolisz, może je odczytać, a Ty możesz odejść, kiedy
  zechcesz.
- **Weryfikowalne, nie tylko obiecane.** Każde pobranie można prześledzić aż do tego publicznego
  kodu źródłowego, a przy każdej zmianie uruchamiane są tysiące automatycznych testów.

Po szczegóły, w tym model bezpieczeństwa połączeń, model zagrożeń oraz sposób weryfikacji
pobrania, zajrzyj do [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md) i
[docs/VERIFYING.md](docs/VERIFYING.md).

## Współtworzenie

Proxion jest oprogramowaniem open source i wkłady są naprawdę mile widziane, od zgłoszeń błędów
po kod. Zacznij od [CONTRIBUTING.md](CONTRIBUTING.md). Jeśli przychodzisz ze społeczności Solid i
coś nie współdziała tak, jak się spodziewasz, to dokładnie ten rodzaj zgłoszenia, o którym
chcemy usłyszeć.

## Dla programistów i osób hostujących samodzielnie

Większości osób wystarczy po prostu użyć instalatora powyżej. Aby pogrzebać w Proxion lub
uruchomić własną, stale działającą bramę (na przykład, aby skierować telefon na swój komputer):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # opcjonalne poświadczenia poda; zostaw puste dla trybu tylko lokalnego
python run_gateway.py
# otwórz http://localhost:8080
```

Zbuduj natywny instalator:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # spakuj bramę dla swojej platformy
cd tauri-app && cargo tauri build # natywny instalator
```

Uruchom testy:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Jak to wszystko się łączy.** Frontend (w `web/`) jest serwowany przez małą **bramę** (w
`proxion-messenger-core/`), która przechowuje Twoje klucze, rozmawia z Twoim podem i łączy się
bezpośrednio z bramami Twoich kontaktów. Na komputerze brama jest wbudowana w aplikację i startuje
razem z nią, więc nigdy jej nie widzisz ani nie instalujesz Pythona. Brama istnieje, ponieważ
Solid obejmuje dane i tożsamość, ale nie dostarczanie na żywo, obecność czy nawiązywanie
połączeń, tę samą rolę pełni homeserver dla Matrix albo serwer SMTP dla poczty e-mail.
Szczegóły w [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) i
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Licencja

[AGPL-3.0](LICENSE). Wolno używać, hostować samodzielnie, forkować i współtworzyć. Jeśli
uruchamiasz zmodyfikowany Proxion jako usługę dla innych, musisz opublikować swoje zmiany. O to
właśnie chodzi: nikt nie może z powrotem zamienić tego w silos.
