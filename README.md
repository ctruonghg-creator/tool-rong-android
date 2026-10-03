# Đảo Rồng Mobile — Android 16 / Xiaomi

Bản này thay toàn bộ UI Tkinter bằng Jetpack Compose và giữ engine Python hiện tại qua Chaquopy. Cấu hình build dùng AGP 8.13.2 + Gradle 8.13 để tương thích compileSdk 36.

## Build GitHub Actions

1. Tạo repository mới trên GitHub.
2. Upload toàn bộ nội dung thư mục này.
3. Vào **Actions → Build Debug APK → Run workflow**.
4. Workflow sẽ tạo artifact `daorong-debug-apk` và file `app-debug.apk`.

## Cấu hình

- compileSdk 36 / targetSdk 36 (Android 16)
- Android Gradle Plugin 8.13.2 / Gradle 8.13 / JDK 17
- minSdk 24
- ABI: `arm64-v8a`, `armeabi-v7a`, `x86_64`
- Chaquopy 16.1.0 / Python 3.13
- Jetpack Compose Material 3
- Không dùng Tkinter trên Android
- Chỉ cho phép HTTPS (`usesCleartextTraffic=false`)

## Lưu ý Xiaomi / Android 16

Ứng dụng nên được chạy ở foreground khi cần automation liên tục. Android 16 có các giới hạn mới đối với công việc chạy nền, còn Xiaomi/HyperOS có thể quản lý pin nghiêm ngặt hơn. Bản này không dùng service nền giả để né giới hạn của hệ điều hành.

## Các chức năng Android đã nối vào engine

Login / Socket.IO, Boss, Event, Tẩy Tủy, Viễn Chinh, Đấu Trường, Lôi Đài, Quà, Thu hoạch, Feed, quét đảo, Ải Thí Luyện, Thủy Quái và Sinh nhật.
