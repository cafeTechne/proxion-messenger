<div align="center">

# Proxion

**真正属于你的私密通讯工具。**

带有真正端到端加密的聊天、语音和视频，你的对话保存在你掌控的存储中，而不是某家公司的服务器
上。构建于开放的 [Solid](https://solidproject.org) 标准之上。无需电话号码，无需注册，中间没有
任何公司。

**阅读其他语言版本：** [English](README.md) · [日本語](README.ja.md) · 中文（简体） · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion 桌面应用：一段端到端加密的对话，侧边栏是房间和联系人" width="800">

</div>

## Proxion 是什么？

Proxion 是一款和你已经在用的通讯工具很像的应用，但有一个改变一切的区别：你的数据属于你自己。

你的消息、文件和通话记录保存在你自己的 **Solid pod** 中，也就是一个你掌控的个人存储空间，而不
是被锁在某家公司的应用里。你可以选择免费的 pod 提供商、使用自己的，或者自行托管，并随时迁移。
你的身份在你的设备上创建，所以没有账户需要注册，也没有什么会泄露。

它是一款真正的日常通讯工具：房间和私信、带屏幕共享的语音和视频通话、文件、表情回应、回复等
等，支持 Windows、macOS、Linux 和网页端。

## 获取 Proxion

**下载并打开即可。** 没有需要配置的东西，也没有需要运行的服务器。

- **Windows、macOS 或 Linux：** 前往[安装页面](https://cafetechne.github.io/proxion-messenger/)
  或[最新版本](../../releases/latest)。
- **使用 [Homebrew](https://brew.sh) 的 macOS：** `brew install cafeTechne/proxion/proxion`
- **在浏览器中：** Proxion 也可作为可安装的网页应用运行。

由于 Proxion 没有经过 Apple 或 Microsoft 签名（这是有意为之，好让你和你自己的软件之间没有任何
把关者），你的系统会在首次打开时显示一次性提示。在 Windows 上选择 *更多信息，然后仍要运行*；在
macOS 上*右键点击，然后打开*；Linux 不会有任何提示。

## 你可以做什么

- **发消息和通话。** 群组房间和私密的一对一聊天，还有带屏幕共享的点对点语音和视频通话。
- **保留你的历史记录。** 一切都以开放格式保存在你的 pod 中，因此它属于你，可以保存、用其他工具
  读取并随身带走。
- **真正私密的对话。** 私信是端到端加密的，你可以通过一起大声读出的一句简短安全短语，确认你真的
  在和你的联系人通话。通话也以同样的方式加密。
- **触达 Solid 上的任何人。** 在更广阔的 Solid 生态中查找并邀请他人，而不仅仅是其他 Proxion
  用户。
- **随处可用。** 桌面、浏览器和移动端，支持离线，支持包括从右到左的阿拉伯语在内的 16 种语言，并且
  可以仅用屏幕阅读器和键盘操作。

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="在手机上运行的 Proxion" width="240">
</p>

## Solid 生态的一部分

Proxion 是一个良好的 Solid 公民，而不是一个只是在底层用了 Solid 的围墙花园。你创建的房间以标准
的 Solid 聊天格式写入，因此其他 Solid 应用可以读取并加入它。

<img src="landing/assets/interop-sidebyside.png" alt="同一个房间在 Proxion 和 SolidOS 数据浏览器中并排显示，消息完全相同" width="900">

- **在 [SolidOS](https://solidos.org) 中打开一个 Proxion 房间**，每条消息都在那里。这在我们的
  测试中是针对真实的 SolidOS 验证过的，而不只是声称。
- **通过 WebID 查找并邀请他人。** 发现某人托管的房间，或者把一份任何 Solid 应用都能读取的邀请放
  进他们的 Solid 收件箱。
- **实时看到新消息和邀请，** 即使 Proxion 已关闭也能收到。
- **你的房间比任何单一服务器都长久。** 房间的结构保存在你的 pod 中，因此可以仅凭你的 pod 重建。

共享房间在设计上是开放的，以便其他应用读取；私密的私信是端到端加密的，并且刻意只有其中的参与者
才能读取。完整的数据格式记录在 [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md)，逐个应用的兼容
性情况在 [docs/INTEROP.md](docs/INTEROP.md)，针对 Solid 规范套件逐条要求的审计在
[docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md)。

## 从设计上就注重隐私

- **端到端加密的**私信和通话，因此中间没有任何中继或服务器能读取它们。
- **你的数据在你的 pod 里，公开可读。** 它是有文档记录的标准数据，而不是一个上锁的二进制块，因此
  任何你授权的应用都能读取，你也可以随时离开。
- **可验证，而不只是承诺。** 每一次下载都可以追溯回这份公开的源代码，并且每次改动都会运行数千个
  自动化测试。

有关细节，包括通话安全模型、威胁模型以及如何验证下载，请参见
[docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md)、[docs/CALLS.md](docs/CALLS.md)、
[SECURITY.md](SECURITY.md) 和 [docs/VERIFYING.md](docs/VERIFYING.md)。

## 参与贡献

Proxion 是开源的，我们真心欢迎各种贡献，从错误报告到代码。请从 [CONTRIBUTING.md](CONTRIBUTING.md)
开始。如果你来自 Solid 社区，并且发现有什么没有按你的预期互操作，那正是我们想听到的那类问题。

## 面向开发者和自托管者

大多数人只需使用上面的安装程序即可。若要折腾 Proxion 或运行你自己的常驻网关（例如让手机连接到你
的桌面）：

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # 可选的 pod 凭据；仅本地使用时留空
python run_gateway.py
# 打开 http://localhost:8080
```

构建原生安装程序：

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # 为你的平台打包网关
cd tauri-app && cargo tauri build # 原生安装程序
```

运行测试：

```bash
cd proxion-messenger-core && pytest    # 后端
cd web && npm test                     # 前端
```

**各部分如何协作。** 前端（在 `web/` 中）由一个小型**网关**（在 `proxion-messenger-core/` 中）
提供服务，它保管你的密钥、与你的 pod 通信，并直接连接到你联系人的网关。在桌面端，网关被打包在应
用内部并随应用一起启动，因此你永远看不到它，也不用安装 Python。网关之所以存在，是因为 Solid 涵盖
了数据和身份，却不涵盖实时投递、在线状态或通话建立，这与 homeserver 之于 Matrix、或 SMTP 服务器
之于电子邮件所扮演的角色相同。详情见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 和
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)。

## 许可证

[AGPL-3.0](LICENSE)。可自由使用、自托管、复刻和贡献。如果你把修改过的 Proxion 作为服务运行给他人
使用，你必须公开你的改动。这正是关键所在：任何人都无法把它重新变回一个孤岛。
