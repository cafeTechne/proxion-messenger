<div align="center">

# Proxion

**Perpesanan pribadi yang benar-benar milik Anda.**

Obrolan, suara, dan video dengan enkripsi ujung ke ujung yang sesungguhnya, tempat percakapan
Anda berada di penyimpanan yang Anda kendalikan, bukan di server sebuah perusahaan. Dibangun di
atas standar terbuka [Solid](https://solidproject.org). Tanpa nomor telepon, tanpa pendaftaran,
tanpa perusahaan di tengah.

**Baca ini dalam bahasa Anda:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · Bahasa Indonesia · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion di desktop: percakapan terenkripsi ujung ke ujung, dengan ruang dan kontak di bilah sisi" width="800">

</div>

## Apa itu Proxion?

Proxion adalah aplikasi perpesanan seperti yang sudah Anda pakai, dengan satu perbedaan yang
mengubah segalanya: data Anda milik Anda.

Pesan, berkas, dan riwayat panggilan Anda berada di **pod Solid** milik Anda sendiri, sebuah
ruang penyimpanan pribadi yang Anda kendalikan, alih-alih terkunci di dalam aplikasi sebuah
perusahaan. Pilih penyedia pod gratis, bawa milik Anda sendiri, atau hosting sendiri, dan
berpindah kapan saja. Identitas Anda dibuat di perangkat Anda, jadi tidak ada akun untuk
didaftarkan dan tidak ada yang bisa bocor.

Ini adalah aplikasi perpesanan sungguhan untuk sehari-hari: ruang dan pesan langsung, panggilan
suara dan video dengan berbagi layar, berkas, reaksi, balasan, dan lainnya, di Windows, macOS,
Linux, dan web.

## Dapatkan Proxion

**Unduh dan buka.** Tidak ada yang perlu dikonfigurasi dan tidak ada server yang perlu
dijalankan.

- **Windows, macOS, atau Linux:** buka [halaman pemasangan](https://cafetechne.github.io/proxion-messenger/)
  atau [rilis terbaru](../../releases/latest).
- **macOS dengan [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **Di peramban Anda:** Proxion juga berjalan sebagai aplikasi web yang dapat dipasang.

Karena Proxion tidak ditandatangani oleh Apple maupun Microsoft (secara sengaja, agar tidak ada
penjaga gerbang di antara Anda dan perangkat lunak Anda sendiri), sistem Anda menampilkan
peringatan sekali saja saat pertama kali membukanya. Di Windows pilih *Info selengkapnya lalu
Tetap jalankan*; di macOS *klik kanan lalu Buka*; Linux tidak menampilkan peringatan apa pun.

## Yang bisa Anda lakukan

- **Berkirim pesan dan menelepon.** Ruang grup dan obrolan pribadi satu lawan satu, ditambah
  panggilan suara dan video peer-to-peer dengan berbagi layar.
- **Simpan riwayat Anda.** Semuanya berada di pod Anda, dalam format terbuka, jadi itu milik Anda
  untuk disimpan, dibaca dengan alat lain, dan dibawa serta.
- **Percakapan yang benar-benar pribadi.** Pesan langsung dienkripsi ujung ke ujung, dan Anda
  bisa memastikan bahwa Anda benar-benar berbicara dengan kontak Anda melalui frasa keamanan
  singkat yang Anda baca bersama dengan lantang. Panggilan dienkripsi dengan cara yang sama.
- **Jangkau siapa pun di Solid.** Temukan dan undang orang di seluruh ekosistem Solid yang lebih
  luas, bukan hanya sesama pengguna Proxion.
- **Pakai di mana saja.** Desktop, peramban, dan ponsel, mampu bekerja luring, dalam 16 bahasa
  termasuk bahasa Arab dari kanan ke kiri, dan dibuat untuk bekerja hanya dengan pembaca layar
  dan papan ketik.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion berjalan di sebuah ponsel" width="240">
</p>

## Bagian dari ekosistem Solid

Proxion adalah warga Solid yang baik, bukan taman berpagar yang sekadar memakai Solid di
baliknya. Ruang yang Anda buat ditulis dalam format obrolan standar Solid, sehingga aplikasi
Solid lain dapat membaca dan bergabung dengannya.

<img src="landing/assets/interop-sidebyside.png" alt="Ruang yang sama ditampilkan berdampingan di Proxion dan di penjelajah data SolidOS, dengan pesan yang sama" width="900">

- **Buka ruang Proxion di [SolidOS](https://solidos.org)** dan setiap pesan ada di sana. Ini
  diverifikasi terhadap SolidOS yang sebenarnya dalam pengujian kami, bukan sekadar klaim.
- **Temukan dan undang orang lewat WebID mereka.** Temukan ruang yang dihosting seseorang, atau
  jatuhkan undangan ke kotak masuk Solid mereka yang dapat dibaca aplikasi Solid mana pun.
- **Lihat pesan dan undangan baru secara langsung,** yang tetap sampai kepada Anda bahkan saat
  Proxion tertutup.
- **Ruang Anda bertahan lebih lama dari server mana pun.** Struktur sebuah ruang berada di pod
  Anda, jadi ia bisa dibangun ulang hanya dari pod Anda.

Ruang bersama bersifat terbuka secara desain agar aplikasi lain dapat membacanya; pesan langsung
pribadi dienkripsi ujung ke ujung dan dengan sengaja hanya dapat dibaca oleh orang-orang di
dalamnya. Format data lengkap didokumentasikan di
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), gambaran kompatibilitas per aplikasi di
[docs/INTEROP.md](docs/INTEROP.md), dan audit per persyaratan terhadap rangkaian spesifikasi
Solid di [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Pribadi secara desain

- **Pesan langsung dan panggilan yang dienkripsi ujung ke ujung,** sehingga tidak ada relai atau
  server di tengah yang bisa membacanya.
- **Data Anda di pod Anda, secara terbuka.** Ini adalah data standar yang terdokumentasi, bukan
  gumpalan terkunci, jadi aplikasi apa pun yang Anda izinkan bisa membacanya dan Anda bisa pergi
  kapan pun Anda mau.
- **Dapat diverifikasi, bukan sekadar dijanjikan.** Setiap unduhan bisa dilacak balik ke kode
  sumber publik ini, dan ribuan pengujian otomatis berjalan pada setiap perubahan.

Untuk detailnya, termasuk model keamanan panggilan, model ancaman, dan cara memverifikasi
unduhan, lihat [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md), [docs/CALLS.md](docs/CALLS.md),
[SECURITY.md](SECURITY.md), dan [docs/VERIFYING.md](docs/VERIFYING.md).

## Berkontribusi

Proxion bersifat sumber terbuka dan kontribusi benar-benar disambut, dari laporan bug hingga
kode. Mulailah dari [CONTRIBUTING.md](CONTRIBUTING.md). Jika Anda datang dari komunitas Solid dan
ada sesuatu yang tidak beroperasi bersama seperti yang Anda harapkan, itu justru jenis masalah
yang ingin kami dengar.

## Untuk pengembang dan yang hosting sendiri

Sebagian besar orang cukup memakai pemasang di atas. Untuk mengutak-atik Proxion atau menjalankan
gateway Anda sendiri yang selalu aktif (misalnya untuk mengarahkan ponsel ke desktop Anda):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # kredensial pod opsional; biarkan kosong untuk mode lokal saja
python run_gateway.py
# buka http://localhost:8080
```

Bangun pemasang native:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # bundel gateway untuk platform Anda
cd tauri-app && cargo tauri build # pemasang native
```

Jalankan pengujian:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Bagaimana semuanya menyatu.** Frontend (di `web/`) dilayani oleh sebuah **gateway** kecil (di
`proxion-messenger-core/`) yang menyimpan kunci Anda, berbicara dengan pod Anda, dan terhubung
langsung ke gateway kontak Anda. Di desktop, gateway dibundel di dalam aplikasi dan mulai
bersamanya, jadi Anda tidak pernah melihatnya atau memasang Python. Gateway ada karena Solid
mencakup data dan identitas tetapi tidak mencakup pengiriman langsung, kehadiran, atau
penyiapan panggilan, peran yang sama seperti yang dimainkan homeserver untuk Matrix atau server
SMTP untuk surel. Detailnya di [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) dan
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Lisensi

[AGPL-3.0](LICENSE). Bebas dipakai, dihosting sendiri, difork, dan dikontribusikan. Jika Anda
menjalankan Proxion yang dimodifikasi sebagai layanan untuk orang lain, Anda harus mempublikasikan
perubahan Anda. Itulah intinya: tidak ada yang bisa mengubahnya kembali menjadi silo.
