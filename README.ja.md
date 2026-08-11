<div align="center">

# Proxion

**本当にあなたのものになる、プライベートなメッセンジャー。**

本物のエンドツーエンド暗号化によるチャット、音声、ビデオ。会話は企業のサーバーではなく、
あなたが管理するストレージに保存されます。オープンな [Solid](https://solidproject.org)
標準の上に構築。電話番号なし、サインアップなし、間に立つ企業なし。

**他の言語で読む：** [English](README.md) · 日本語 · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion デスクトップアプリ: エンドツーエンドで暗号化された会話。サイドバーにルームと連絡先" width="800">

</div>

## Proxion とは？

Proxion はあなたがすでに使っているようなメッセンジャーですが、すべてを変える一つの違いが
あります。データはあなたのものです。

メッセージ、ファイル、通話履歴は、企業のアプリの中に閉じ込められるのではなく、あなたが管理
する個人用ストレージである自分の **Solid ポッド** に保存されます。無料のポッドプロバイダーを
選ぶ、自分のものを持ち込む、または自分で運用する、いつでも移動できます。あなたのアイデン
ティティは自分の端末で作られるため、登録するアカウントもなく、漏れるものもありません。

これは日常使いの本物のメッセンジャーです。ルームとダイレクトメッセージ、画面共有付きの音声・
ビデオ通話、ファイル、リアクション、返信など、Windows、macOS、Linux、そしてウェブで使えます。

## Proxion を入手する

**ダウンロードして開くだけ。** 設定するものも、動かすサーバーもありません。

- **Windows、macOS、Linux:** [インストールページ](https://cafetechne.github.io/proxion-messenger/)
  または [最新リリース](../../releases/latest) を入手してください。
- **[Homebrew](https://brew.sh) を使う macOS:** `brew install cafeTechne/proxion/proxion`
- **ブラウザで:** Proxion はインストール可能なウェブアプリとしても動作します。

Proxion は Apple や Microsoft の署名を受けていないため（意図的に、あなたと自分のソフトウェア
の間に誰も立たないように）、初回起動時に OS が一度だけ確認を表示します。Windows では *詳細情報
から実行* を、macOS では *右クリックから開く* を選んでください。Linux では確認は表示されません。

## できること

- **メッセージと通話。** グループルームとプライベートな1対1のチャット、さらに画面共有付きの
  ピアツーピア音声・ビデオ通話。
- **履歴を保持。** すべてがオープンな形式であなたのポッドに保存されるため、保管し、他のツール
  で読み、持ち運べます。
- **本当にプライベートな会話。** ダイレクトメッセージはエンドツーエンドで暗号化され、一緒に声
  に出して読み上げる短い安全確認フレーズで、本当に相手と話していることを確認できます。通話も
  同じ方法で暗号化されます。
- **Solid 上の誰にでも届く。** Proxion のユーザーだけでなく、より広い Solid エコシステムの
  人々を見つけて招待できます。
- **どこでも使える。** デスクトップ、ブラウザ、モバイル。オフライン対応、右から左に書くアラビ
  ア語を含む6言語対応。スクリーンリーダーとキーボードだけで操作できるように作られています。

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="スマートフォンで動作する Proxion" width="240">
</p>

## Solid エコシステムの一部

Proxion は、内部で Solid を使っているだけの壁に囲まれた庭ではなく、良き Solid の一員です。
あなたが作るルームは標準の Solid チャット形式で書かれるため、他の Solid アプリが読んで参加
できます。

<img src="landing/assets/interop-sidebyside.png" alt="Proxion と SolidOS データブラウザーで並べて表示された同じルーム。同じメッセージが表示されている" width="900">

- **[SolidOS](https://solidos.org) で Proxion のルームを開く** と、すべてのメッセージがそこに
  あります。これは主張ではなく、私たちのテストで実際の SolidOS に対して検証されています。
- **WebID で人を見つけて招待する。** 誰かがホストするルームを見つけたり、どの Solid アプリで
  も読める招待をその人の Solid 受信箱に置いたりできます。
- **新しいメッセージと招待をリアルタイムで確認、** Proxion を閉じていても届きます。
- **ルームは特定のサーバーより長生き。** ルームの構造はあなたのポッドに保存されるため、ポッド
  だけから再構築できます。

共有ルームは他のアプリが読めるように設計上オープンです。プライベートなダイレクトメッセージは
エンドツーエンドで暗号化され、意図的にその参加者だけが読めます。完全なデータ形式は
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md) に、アプリごとの互換性は
[docs/INTEROP.md](docs/INTEROP.md) に、Solid 仕様スイートに対する要件ごとの監査は
[docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md) に記載されています。

## 設計からしてプライベート

- **エンドツーエンドで暗号化された** ダイレクトメッセージと通話。間に立つリレーやサーバーは
  読めません。
- **あなたのデータはあなたのポッドに、オープンな形で。** ロックされた塊ではなく、文書化された
  標準的なデータなので、あなたが許可したどのアプリでも読め、いつでも立ち去れます。
- **約束ではなく、検証可能。** すべてのダウンロードはこの公開ソースコードまで遡って確認でき、
  変更のたびに何千もの自動テストが実行されます。

通話のセキュリティモデル、脅威モデル、ダウンロードの検証方法などの詳細は、
[docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md)、[docs/CALLS.md](docs/CALLS.md)、
[SECURITY.md](SECURITY.md)、[docs/VERIFYING.md](docs/VERIFYING.md) を参照してください。

## 貢献する

Proxion はオープンソースで、バグ報告からコードまで、貢献を心から歓迎します。
[CONTRIBUTING.md](CONTRIBUTING.md) から始めてください。Solid コミュニティから来て、何かが
期待どおりに相互運用しない場合、それはまさに私たちが聞きたい種類の問題です。

## 開発者とセルフホスト向け

ほとんどの人は上のインストーラーを使うだけで十分です。Proxion をいじったり、自分の常時稼働
ゲートウェイを動かしたりするには（例えば、スマートフォンをデスクトップに向けるため）:

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # ポッドの認証情報は任意。ローカルのみなら空欄のままで
python run_gateway.py
# http://localhost:8080 を開く
```

ネイティブインストーラーをビルドする:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # 自分のプラットフォーム用にゲートウェイをバンドル
cd tauri-app && cargo tauri build # ネイティブインストーラー
```

テストを実行する:

```bash
cd proxion-messenger-core && pytest    # バックエンド
cd web && npm test                     # フロントエンド
```

**どう組み合わさっているか。** フロントエンド（`web/` 内）は、鍵を保持し、ポッドと通信し、
連絡先のゲートウェイに直接接続する小さな **ゲートウェイ**（`proxion-messenger-core/` 内）から
配信されます。デスクトップではゲートウェイはアプリの中に同梱されて一緒に起動するため、あなた
はそれを見ることも Python をインストールすることもありません。ゲートウェイが存在するのは、
Solid がデータとアイデンティティは扱う一方で、ライブ配信、プレゼンス、通話のセットアップは
扱わないからです。これは Matrix におけるホームサーバー、あるいはメールにおける SMTP サーバー
と同じ役割です。詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) と
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) にあります。

## ライセンス

[AGPL-3.0](LICENSE)。使用、セルフホスト、フォーク、貢献は自由です。改変した Proxion を他者
向けのサービスとして運用する場合は、変更を公開する必要があります。それが要点です。誰もこれを
再びサイロに戻すことはできません。
