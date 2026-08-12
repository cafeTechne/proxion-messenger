<div align="center">

# Proxion

**Gerçekten size ait olan özel mesajlaşma.**

Gerçek uçtan uca şifrelemeyle sohbet, sesli ve görüntülü görüşme; konuşmalarınız bir şirketin
sunucularında değil, sizin denetlediğiniz bir depolama alanında yaşar. Açık
[Solid](https://solidproject.org) standardı üzerine kuruludur. Telefon numarası yok, kayıt yok,
arada hiçbir şirket yok.

**Bunu kendi dilinizde okuyun:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · Türkçe · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Masaüstünde Proxion: uçtan uca şifrelenmiş bir konuşma, kenar çubuğunda odalar ve kişiler" width="800">

</div>

## Proxion nedir?

Proxion, zaten kullandıklarınıza benzeyen bir mesajlaşma uygulamasıdır, ancak her şeyi
değiştiren bir farkı vardır: verileriniz size aittir.

Mesajlarınız, dosyalarınız ve arama geçmişiniz, bir şirketin uygulamasının içine kilitlenmek
yerine, denetlediğiniz kişisel bir depolama alanı olan kendi **Solid pod**'unuzda yaşar.
Ücretsiz bir pod sağlayıcısı seçin, kendinizinkini getirin ya da kendiniz barındırın ve
istediğiniz zaman taşının. Kimliğiniz kendi cihazınızda oluşturulur, bu yüzden kaydolacak bir
hesap ve sızacak bir şey yoktur.

Bu, gerçek ve günlük bir mesajlaşma uygulamasıdır: odalar ve doğrudan mesajlar, ekran paylaşımlı
sesli ve görüntülü görüşmeler, dosyalar, tepkiler, yanıtlar ve daha fazlası; Windows, macOS,
Linux ve web üzerinde.

## Proxion'u edinin

**İndirin ve açın.** Yapılandırılacak bir şey ya da çalıştırılacak bir sunucu yoktur.

- **Windows, macOS veya Linux:** [kurulum sayfasına](https://cafetechne.github.io/proxion-messenger/)
  ya da [en son sürüme](../../releases/latest) gidin.
- **[Homebrew](https://brew.sh) ile macOS:** `brew install cafeTechne/proxion/proxion`
- **Tarayıcınızda:** Proxion, kurulabilir bir web uygulaması olarak da çalışır.

Proxion, Apple veya Microsoft tarafından imzalanmadığı için (sizinle kendi yazılımınız arasına
hiçbir bekçi girmesin diye bilerek), sistemi ilk açtığınızda tek seferlik bir uyarı gösterir.
Windows'ta *Ek bilgi, ardından Yine de çalıştır*; macOS'te *sağ tıklayın, ardından Aç* seçin;
Linux hiçbir uyarı göstermez.

## Neler yapabilirsiniz

- **Mesajlaşın ve arayın.** Grup odaları ve özel bire bir sohbetler, ayrıca ekran paylaşımlı
  eşten eşe sesli ve görüntülü görüşmeler.
- **Geçmişinizi saklayın.** Her şey açık bir biçimde pod'unuzda yaşar, bu yüzden saklamak, başka
  araçlarla okumak ve yanınızda taşımak üzere sizindir.
- **Gerçekten özel konuşmalar.** Doğrudan mesajlar uçtan uca şifrelenir ve birlikte yüksek sesle
  okuduğunuz kısa bir güvenlik ifadesiyle gerçekten kişinizle konuştuğunuzu doğrulayabilirsiniz.
  Aramalar da aynı şekilde şifrelenir.
- **Solid üzerindeki herkese ulaşın.** Yalnızca diğer Proxion kullanıcılarına değil, daha geniş
  Solid ekosistemindeki kişilere ulaşın ve onları davet edin.
- **Her yerde kullanın.** Masaüstü, tarayıcı ve telefon; çevrimdışı çalışabilen, sağdan sola
  yazılan Arapça dahil 16 dilde ve yalnızca ekran okuyucu ve klavyeyle çalışacak biçimde
  tasarlanmış.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Bir telefonda çalışan Proxion" width="240">
</p>

## Solid ekosisteminin bir parçası

Proxion, Solid'i yalnızca arka planda kullanan duvarlarla çevrili bir bahçe değil, iyi bir Solid
yurttaşıdır. Oluşturduğunuz bir oda standart Solid sohbet biçiminde yazılır, böylece diğer Solid
uygulamaları onu okuyabilir ve ona katılabilir.

<img src="landing/assets/interop-sidebyside.png" alt="Aynı oda Proxion'da ve SolidOS veri tarayıcısında yan yana, aynı mesajlarla gösteriliyor" width="900">

- **Bir Proxion odasını [SolidOS](https://solidos.org) içinde açın** ve her mesaj oradadır. Bu,
  yalnızca iddia edilmez, testlerimizde gerçek SolidOS'e karşı doğrulanır.
- **Kişileri WebID'leriyle bulun ve davet edin.** Birinin barındırdığı odaları keşfedin ya da
  herhangi bir Solid uygulamasının okuyabileceği bir daveti onun Solid gelen kutusuna bırakın.
- **Yeni mesajları ve davetleri gerçek zamanlı görün,** Proxion kapalıyken bile size ulaşırlar.
- **Odalarınız tek bir sunucudan uzun ömürlüdür.** Bir odanın yapısı pod'unuzda yaşar, bu yüzden
  yalnızca pod'unuzdan yeniden oluşturulabilir.

Paylaşılan odalar, başka uygulamaların okuyabilmesi için tasarım gereği açıktır; özel doğrudan
mesajlar uçtan uca şifrelenir ve bilerek yalnızca içindeki kişilerce okunabilir. Tam veri biçimi
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md) içinde, uygulama uygulama uyumluluk tablosu
[docs/INTEROP.md](docs/INTEROP.md) içinde ve Solid belirtim paketine karşı gereksinim gereksinim
bir denetim [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md) içinde belgelenmiştir.

## Tasarımı gereği özel

- **Uçtan uca şifrelenmiş** doğrudan mesajlar ve aramalar, böylece aradaki hiçbir aktarıcı ya da
  sunucu onları okuyamaz.
- **Verileriniz pod'unuzda, açıkça.** Bunlar kilitli bir yığın değil, belgelenmiş ve standart
  verilerdir, bu yüzden izin verdiğiniz her uygulama onları okuyabilir ve istediğiniz zaman
  ayrılabilirsiniz.
- **Vaat değil, doğrulanabilir.** Her indirme bu herkese açık kaynak koduna kadar izlenebilir ve
  her değişiklikte binlerce otomatik test çalışır.

Arama güvenlik modeli, tehdit modeli ve bir indirmenin nasıl doğrulanacağı dahil ayrıntılar için
[docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md), [docs/CALLS.md](docs/CALLS.md),
[SECURITY.md](SECURITY.md) ve [docs/VERIFYING.md](docs/VERIFYING.md) belgelerine bakın.

## Katkıda bulunma

Proxion açık kaynaktır ve hata bildirimlerinden koda kadar katkılar gerçekten memnuniyetle
karşılanır. [CONTRIBUTING.md](CONTRIBUTING.md) ile başlayın. Solid topluluğundan geliyorsanız ve
bir şey beklediğiniz gibi birlikte çalışmıyorsa, duymak istediğimiz sorun tam olarak budur.

## Geliştiriciler ve kendi kendine barındıranlar için

Çoğu kişinin yukarıdaki yükleyiciyi kullanması yeterlidir. Proxion'u kurcalamak ya da kendi
sürekli çalışan ağ geçidinizi çalıştırmak için (örneğin bir telefonu masaüstünüze yöneltmek
için):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # isteğe bağlı pod kimlik bilgileri; yalnızca yerel için boş bırakın
python run_gateway.py
# http://localhost:8080 adresini açın
```

Yerel bir yükleyici oluşturun:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # ağ geçidini platformunuz için paketleyin
cd tauri-app && cargo tauri build # yerel yükleyici
```

Testleri çalıştırın:

```bash
cd proxion-messenger-core && pytest    # arka uç
cd web && npm test                     # ön uç
```

**Her şey nasıl bir araya geliyor.** Ön uç (`web/` içinde), anahtarlarınızı tutan, pod'unuzla
konuşan ve kişilerinizin ağ geçitlerine doğrudan bağlanan küçük bir **ağ geçidi** (`proxion-
messenger-core/` içinde) tarafından sunulur. Masaüstünde ağ geçidi uygulamanın içine paketlenir
ve onunla başlar, bu yüzden onu hiç görmez ve Python kurmazsınız. Ağ geçidi vardır çünkü Solid
veriyi ve kimliği kapsar ama canlı iletimi, çevrimiçi durumu ya da arama kurulumunu kapsamaz; bu,
Matrix için bir homeserver'ın ya da e-posta için bir SMTP sunucusunun oynadığı rolün aynısıdır.
Ayrıntılar [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ve
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) içinde.

## Lisans

[AGPL-3.0](LICENSE). Kullanmakta, kendiniz barındırmakta, çatallamakta ve katkıda bulunmakta
özgürsünüz. Değiştirdiğiniz bir Proxion'u başkaları için bir hizmet olarak çalıştırırsanız,
değişikliklerinizi yayımlamanız gerekir. Amaç budur: kimse onu yeniden bir siloya
dönüştüremez.
