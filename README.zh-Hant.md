<div align="center">

# Proxion

**真正屬於你的私密通訊工具。**

具備真正端對端加密的聊天、語音與視訊，你的對話儲存在你掌控的儲存空間中，而不是某家公司的
伺服器上。建構於開放的 [Solid](https://solidproject.org) 標準之上。無需電話號碼，無需註冊，
中間沒有任何公司。

**閱讀其他語言版本：** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · 中文（繁體） · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion 桌面應用程式：一段端對端加密的對話，側邊欄是聊天室與聯絡人" width="800">

</div>

## Proxion 是什麼？

Proxion 是一款和你已經在用的通訊工具很像的應用程式，但有一個改變一切的差別：你的資料屬於你
自己。

你的訊息、檔案與通話紀錄儲存在你自己的 **Solid pod** 中，也就是一個你掌控的個人儲存空間，而不
是被鎖在某家公司的應用程式裡。你可以選擇免費的 pod 供應商、使用自己的，或自行架設，並隨時搬遷。
你的身分在你的裝置上建立，因此沒有帳號需要註冊，也沒有什麼會外洩。

它是一款真正的日常通訊工具：聊天室與私訊、帶螢幕分享的語音與視訊通話、檔案、表情回應、回覆等
等，支援 Windows、macOS、Linux 與網頁端。

## 取得 Proxion

**下載並開啟即可。** 沒有需要設定的東西，也沒有需要執行的伺服器。

- **Windows、macOS 或 Linux：** 前往[安裝頁面](https://cafetechne.github.io/proxion-messenger/)
  或[最新版本](../../releases/latest)。
- **使用 [Homebrew](https://brew.sh) 的 macOS：** `brew install cafeTechne/proxion/proxion`
- **在瀏覽器中：** Proxion 也可作為可安裝的網頁應用程式執行。

由於 Proxion 沒有經過 Apple 或 Microsoft 簽署（這是刻意為之，好讓你和你自己的軟體之間沒有任何
把關者），你的系統會在首次開啟時顯示一次性提示。在 Windows 上選擇 *更多資訊，然後仍要執行*；在
macOS 上*按右鍵，然後開啟*；Linux 不會有任何提示。

## 你可以做什麼

- **傳訊與通話。** 群組聊天室與私密的一對一聊天，還有帶螢幕分享的點對點語音與視訊通話。
- **保留你的紀錄。** 一切都以開放格式儲存在你的 pod 中，因此它屬於你，可以保存、用其他工具讀取
  並隨身帶走。
- **真正私密的對話。** 私訊是端對端加密的，你可以透過一起大聲讀出的一句簡短安全短語，確認你真的
  在和你的聯絡人通話。通話也以同樣的方式加密。
- **觸及 Solid 上的任何人。** 在更廣闊的 Solid 生態系中尋找並邀請他人，而不只是其他 Proxion
  使用者。
- **隨處可用。** 桌面、瀏覽器與行動裝置，支援離線，支援包括由右至左的阿拉伯文在內的 16 種語言，並
  且可以只用螢幕閱讀器與鍵盤操作。

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="在手機上執行的 Proxion" width="240">
</p>

## Solid 生態系的一部分

Proxion 是一個良好的 Solid 公民，而不是一個只是在底層用了 Solid 的圍牆花園。你建立的聊天室以標
準的 Solid 聊天格式寫入，因此其他 Solid 應用程式可以讀取並加入它。

<img src="landing/assets/interop-sidebyside.png" alt="同一個聊天室在 Proxion 與 SolidOS 資料瀏覽器中並排顯示，訊息完全相同" width="900">

- **在 [SolidOS](https://solidos.org) 中開啟一個 Proxion 聊天室**，每則訊息都在那裡。這在我們的
  測試中是針對真實的 SolidOS 驗證過的，而不只是聲稱。
- **透過 WebID 尋找並邀請他人。** 探索某人所主持的聊天室，或把一份任何 Solid 應用程式都能讀取的
  邀請放進他們的 Solid 收件匣。
- **即時看到新訊息與邀請，** 即使 Proxion 已關閉也能收到。
- **你的聊天室比任何單一伺服器都長久。** 聊天室的結構儲存在你的 pod 中，因此可以僅憑你的 pod
  重建。

共享聊天室在設計上是開放的，以便其他應用程式讀取；私密的私訊是端對端加密的，並且刻意只有其中的
參與者才能讀取。完整的資料格式記錄在 [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md)，逐個應用
程式的相容性情況在 [docs/INTEROP.md](docs/INTEROP.md)，針對 Solid 規範套件逐條要求的稽核在
[docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md)。

## 從設計上就重視隱私

- **端對端加密的**私訊與通話，因此中間沒有任何中繼或伺服器能讀取它們。
- **你的資料在你的 pod 裡，公開可讀。** 它是有文件記錄的標準資料，而不是一個上鎖的二進位區塊，因
  此任何你授權的應用程式都能讀取，你也可以隨時離開。
- **可驗證，而不只是承諾。** 每一次下載都可以追溯回這份公開的原始碼，並且每次改動都會執行數千個
  自動化測試。

有關細節，包括通話安全模型、威脅模型以及如何驗證下載，請參見
[docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md)、[docs/CALLS.md](docs/CALLS.md)、
[SECURITY.md](SECURITY.md) 與 [docs/VERIFYING.md](docs/VERIFYING.md)。

## 參與貢獻

Proxion 是開源的，我們真心歡迎各種貢獻，從錯誤回報到程式碼。請從 [CONTRIBUTING.md](CONTRIBUTING.md)
開始。如果你來自 Solid 社群，並且發現有什麼沒有按你的預期互通，那正是我們想聽到的那類問題。

## 給開發者與自行架設者

大多數人只需使用上面的安裝程式即可。若要修改 Proxion 或執行你自己的常駐閘道（例如讓手機連線到你
的桌面）：

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # 選用的 pod 憑證；僅本機使用時留空
python run_gateway.py
# 開啟 http://localhost:8080
```

建置原生安裝程式：

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # 為你的平台打包閘道
cd tauri-app && cargo tauri build # 原生安裝程式
```

執行測試：

```bash
cd proxion-messenger-core && pytest    # 後端
cd web && npm test                     # 前端
```

**各部分如何協作。** 前端（在 `web/` 中）由一個小型**閘道**（在 `proxion-messenger-core/` 中）
提供服務，它保管你的金鑰、與你的 pod 通訊，並直接連線到你聯絡人的閘道。在桌面端，閘道被打包在應
用程式內部並隨應用程式一起啟動，因此你永遠看不到它，也不用安裝 Python。閘道之所以存在，是因為
Solid 涵蓋了資料與身分，卻不涵蓋即時傳遞、上線狀態或通話建立，這與 homeserver 之於 Matrix、或
SMTP 伺服器之於電子郵件所扮演的角色相同。詳情見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 與
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)。

## 授權條款

[AGPL-3.0](LICENSE)。可自由使用、自行架設、分支與貢獻。如果你把修改過的 Proxion 作為服務執行給
他人使用，你必須公開你的改動。這正是關鍵所在：任何人都無法把它重新變回一座孤島。
