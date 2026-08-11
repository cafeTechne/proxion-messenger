<div align="center">

# Proxion

**Nhắn tin riêng tư thực sự thuộc về bạn.**

Trò chuyện, thoại và video với mã hóa đầu cuối thực sự, nơi các cuộc trò chuyện của bạn nằm
trong bộ lưu trữ do bạn kiểm soát chứ không phải trên máy chủ của một công ty. Được xây dựng
trên tiêu chuẩn mở [Solid](https://solidproject.org). Không số điện thoại, không đăng ký, không
có công ty nào ở giữa.

**Đọc bằng ngôn ngữ của bạn:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · Tiếng Việt · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion trên máy tính: một cuộc trò chuyện được mã hóa đầu cuối, với các phòng và danh bạ ở thanh bên" width="800">

</div>

## Proxion là gì?

Proxion là một ứng dụng nhắn tin giống những ứng dụng bạn đang dùng, nhưng có một khác biệt thay
đổi mọi thứ: dữ liệu là của bạn.

Tin nhắn, tập tin và lịch sử cuộc gọi của bạn nằm trong **pod Solid** của riêng bạn, một không
gian lưu trữ cá nhân do bạn kiểm soát, thay vì bị khóa bên trong ứng dụng của một công ty. Hãy
chọn một nhà cung cấp pod miễn phí, mang pod của riêng bạn, hoặc tự lưu trữ, và chuyển đi bất cứ
lúc nào. Danh tính của bạn được tạo trên thiết bị của bạn, nên không có tài khoản nào để đăng ký
và không có gì để rò rỉ.

Đây là một ứng dụng nhắn tin thực thụ cho mỗi ngày: phòng và tin nhắn trực tiếp, cuộc gọi thoại
và video có chia sẻ màn hình, tập tin, biểu cảm, trả lời và hơn thế nữa, trên Windows, macOS,
Linux và web.

## Tải Proxion

**Tải về và mở.** Không có gì để cấu hình và không có máy chủ nào để chạy.

- **Windows, macOS hoặc Linux:** vào [trang cài đặt](https://cafetechne.github.io/proxion-messenger/)
  hoặc [bản phát hành mới nhất](../../releases/latest).
- **macOS với [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **Trong trình duyệt:** Proxion cũng chạy như một ứng dụng web có thể cài đặt.

Vì Proxion không được Apple hay Microsoft ký (một cách có chủ đích, để không có người gác cổng
nào đứng giữa bạn và phần mềm của chính bạn), hệ thống sẽ hiện một lời nhắc một lần trong lần mở
đầu tiên. Trên Windows chọn *Thông tin thêm rồi Vẫn chạy*; trên macOS *nhấp chuột phải rồi Mở*;
Linux không hiện lời nhắc nào.

## Bạn có thể làm gì

- **Nhắn tin và gọi.** Phòng nhóm và trò chuyện riêng một đối một, cùng với cuộc gọi thoại và
  video ngang hàng có chia sẻ màn hình.
- **Giữ lịch sử của bạn.** Mọi thứ nằm trong pod của bạn, ở định dạng mở, nên nó là của bạn để
  lưu giữ, đọc bằng công cụ khác và mang theo bên mình.
- **Trò chuyện thực sự riêng tư.** Tin nhắn trực tiếp được mã hóa đầu cuối, và bạn có thể xác
  nhận mình thật sự đang nói chuyện với người liên hệ bằng một cụm từ an toàn ngắn mà cả hai cùng
  đọc to. Cuộc gọi cũng được mã hóa theo cách tương tự.
- **Kết nối bất kỳ ai trên Solid.** Tìm và mời mọi người trên khắp hệ sinh thái Solid rộng lớn
  hơn, không chỉ những người dùng Proxion khác.
- **Dùng ở bất cứ đâu.** Máy tính, trình duyệt và điện thoại, có thể hoạt động ngoại tuyến, bằng
  sáu ngôn ngữ bao gồm tiếng Ả Rập viết từ phải sang trái, và được thiết kế để hoạt động chỉ với
  trình đọc màn hình và bàn phím.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion đang chạy trên điện thoại" width="240">
</p>

## Một phần của hệ sinh thái Solid

Proxion là một công dân Solid tốt, không phải một khu vườn có tường bao chỉ dùng Solid ở bên
dưới. Một phòng bạn tạo được ghi theo định dạng trò chuyện chuẩn của Solid, nên các ứng dụng
Solid khác có thể đọc và tham gia nó.

<img src="landing/assets/interop-sidebyside.png" alt="Cùng một phòng hiển thị cạnh nhau trong Proxion và trong trình duyệt dữ liệu SolidOS, với cùng các tin nhắn" width="900">

- **Mở một phòng Proxion trong [SolidOS](https://solidos.org)** và mọi tin nhắn đều ở đó. Điều
  này được kiểm chứng với SolidOS thật trong các bài kiểm thử của chúng tôi, không chỉ là lời
  tuyên bố.
- **Tìm và mời mọi người bằng WebID của họ.** Khám phá các phòng mà ai đó lưu trữ, hoặc để lại
  một lời mời trong hộp thư Solid của họ mà bất kỳ ứng dụng Solid nào cũng đọc được.
- **Xem tin nhắn và lời mời mới theo thời gian thực,** chúng đến với bạn ngay cả khi Proxion đã
  đóng.
- **Các phòng của bạn tồn tại lâu hơn bất kỳ máy chủ đơn lẻ nào.** Cấu trúc của một phòng nằm
  trong pod của bạn, nên nó có thể được dựng lại chỉ từ pod của bạn.

Các phòng chung được mở theo thiết kế để ứng dụng khác có thể đọc; tin nhắn trực tiếp riêng tư
được mã hóa đầu cuối và có chủ đích chỉ những người trong đó mới đọc được. Định dạng dữ liệu đầy
đủ được ghi trong [docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), bức tranh tương thích theo
từng ứng dụng trong [docs/INTEROP.md](docs/INTEROP.md), và một cuộc kiểm toán theo từng yêu cầu
đối chiếu với bộ đặc tả Solid trong [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Riêng tư theo thiết kế

- **Tin nhắn trực tiếp và cuộc gọi được mã hóa đầu cuối,** nên không có bộ chuyển tiếp hay máy
  chủ nào ở giữa có thể đọc chúng.
- **Dữ liệu của bạn nằm trong pod của bạn, một cách công khai.** Đó là dữ liệu chuẩn, có tài
  liệu, không phải một khối bị khóa, nên bất kỳ ứng dụng nào bạn cho phép đều đọc được và bạn có
  thể rời đi bất cứ lúc nào.
- **Có thể kiểm chứng, không chỉ là hứa hẹn.** Mỗi lần tải về đều có thể truy ngược về mã nguồn
  công khai này, và hàng nghìn bài kiểm thử tự động chạy với mỗi thay đổi.

Để biết chi tiết, bao gồm mô hình bảo mật cuộc gọi, mô hình mối đe dọa và cách xác minh một bản
tải về, xem [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md), [docs/CALLS.md](docs/CALLS.md),
[SECURITY.md](SECURITY.md) và [docs/VERIFYING.md](docs/VERIFYING.md).

## Đóng góp

Proxion là mã nguồn mở và các đóng góp thực sự được hoan nghênh, từ báo cáo lỗi đến mã nguồn. Hãy
bắt đầu với [CONTRIBUTING.md](CONTRIBUTING.md). Nếu bạn đến từ cộng đồng Solid và có điều gì đó
không tương tác như bạn mong đợi, thì đó chính là loại vấn đề mà chúng tôi muốn nghe.

## Dành cho nhà phát triển và người tự lưu trữ

Hầu hết mọi người chỉ cần dùng trình cài đặt ở trên. Để nghịch Proxion hoặc chạy cổng luôn bật
của riêng bạn (ví dụ để hướng một điện thoại tới máy tính của bạn):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # thông tin đăng nhập pod tùy chọn; để trống nếu chỉ dùng cục bộ
python run_gateway.py
# mở http://localhost:8080
```

Xây dựng trình cài đặt gốc:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # đóng gói cổng cho nền tảng của bạn
cd tauri-app && cargo tauri build # trình cài đặt gốc
```

Chạy các bài kiểm thử:

```bash
cd proxion-messenger-core && pytest    # phần phụ trợ
cd web && npm test                     # phần giao diện
```

**Mọi thứ khớp với nhau ra sao.** Phần giao diện (trong `web/`) được phục vụ bởi một **cổng**
nhỏ (trong `proxion-messenger-core/`) giữ khóa của bạn, trò chuyện với pod của bạn và kết nối
trực tiếp tới cổng của những người liên hệ. Trên máy tính, cổng được đóng gói bên trong ứng dụng
và khởi động cùng nó, nên bạn không bao giờ thấy nó hay phải cài Python. Cổng tồn tại vì Solid
bao quát dữ liệu và danh tính nhưng không bao quát việc chuyển phát trực tiếp, trạng thái hiện
diện hay thiết lập cuộc gọi, cùng vai trò mà một homeserver đảm nhận cho Matrix hay một máy chủ
SMTP đảm nhận cho email. Chi tiết trong [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) và
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Giấy phép

[AGPL-3.0](LICENSE). Tự do sử dụng, tự lưu trữ, rẽ nhánh và đóng góp. Nếu bạn chạy một Proxion đã
sửa đổi như một dịch vụ cho người khác, bạn phải công bố các thay đổi của mình. Đó chính là điểm
mấu chốt: không ai có thể biến nó trở lại thành một ốc đảo đóng kín.
