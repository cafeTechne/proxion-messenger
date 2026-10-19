// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{
    AppHandle, CustomMenuItem, Manager, State, SystemTray, SystemTrayEvent,
    SystemTrayMenu, SystemTrayMenuItem,
};
use tauri_plugin_autostart::MacosLauncher;
use serde::{Deserialize, Serialize};

// ── Shared state ─────────────────────────────────────────────────────────────

struct GatewayChild(Mutex<Option<tauri::api::process::CommandChild>>);

#[derive(Default)]
struct GatewayFlags {
    /// The sidecar process is alive (cleared on its Terminated event).
    running: AtomicBool,
    /// We asked it to stop, so its exit is not a crash.
    stopping: AtomicBool,
}
struct PendingDeepLink(Mutex<Option<String>>);
struct TrayUnreadState(Mutex<Vec<UnreadContact>>);

#[derive(Clone, Serialize, Deserialize, Debug)]
struct UnreadContact {
    name: String,
    count: u32,
    thread_id: String,
}

// ── Keychain helpers ──────────────────────────────────────────────────────────

fn kentry(key: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new("dev.proxion.app", key).map_err(|e| e.to_string())
}

fn kread(key: &str) -> Option<String> {
    kentry(key).ok().and_then(|e| e.get_password().ok())
}

// The gateway starts with these at every launch, so a script in the webview
// must not be able to repoint them silently: a new pod or account needs a
// native yes from the user. Async so the blocking dialog is off the main thread.
#[tauri::command]
async fn store_pod_credentials(
    window: tauri::Window,
    css_url: String,
    email: String,
    password: String,
) -> Result<(), String> {
    let same = kread("pod_css_url").as_deref() == Some(css_url.as_str())
        && kread("pod_email").as_deref() == Some(email.as_str());
    if !same {
        let msg = format!(
            "Save the sign-in for {email} on {css_url}? Proxion will connect to this pod each time it starts."
        );
        if !tauri::api::dialog::blocking::ask(Some(&window), "Save pod sign-in", msg) {
            return Err("declined".into());
        }
    }
    kentry("pod_css_url")?.set_password(&css_url).map_err(|e| e.to_string())?;
    kentry("pod_email")?.set_password(&email).map_err(|e| e.to_string())?;
    kentry("pod_password")?.set_password(&password).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn clear_pod_credentials() -> Result<(), String> {
    for key in &["pod_css_url", "pod_email", "pod_password"] {
        let entry = kentry(key)?;
        match entry.delete_password() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(())
}

// ── Notifications ─────────────────────────────────────────────────────────────

#[tauri::command]
fn show_notification(title: String, body: String) {
    tauri::api::notification::Notification::new("dev.proxion.app")
        .title(&title)
        .body(&body)
        .show()
        .ok();
}

// ── Tray ──────────────────────────────────────────────────────────────────────

fn build_tray_menu(unread: &[UnreadContact]) -> SystemTrayMenu {
    let mut menu = SystemTrayMenu::new();
    for contact in unread.iter().take(3) {
        let label = format!("{} ({})", contact.name, contact.count);
        let id = format!("unread:{}", contact.thread_id);
        menu = menu.add_item(CustomMenuItem::new(id, label));
    }
    if !unread.is_empty() {
        menu = menu.add_native_item(SystemTrayMenuItem::Separator);
    }
    let total: u32 = unread.iter().map(|c| c.count).sum();
    let open_label = if total > 0 {
        format!("Open Proxion ({})", total)
    } else {
        "Open Proxion".to_string()
    };
    menu.add_item(CustomMenuItem::new("open", open_label))
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(CustomMenuItem::new("quit", "Quit Proxion"))
}

fn build_tray() -> SystemTray {
    SystemTray::new().with_menu(build_tray_menu(&[]))
}

#[tauri::command]
fn update_tray_unread(
    app: AppHandle,
    contacts: Vec<UnreadContact>,
    state: State<TrayUnreadState>,
) -> Result<(), String> {
    *state.0.lock().unwrap() = contacts.clone();
    app.tray_handle()
        .set_menu(build_tray_menu(&contacts))
        .map_err(|e| e.to_string())
}

// ── Deep link ─────────────────────────────────────────────────────────────────

#[tauri::command]
fn consume_pending_deep_link(state: State<PendingDeepLink>) -> Option<String> {
    state.0.lock().unwrap().take()
}

const AUTOSTART_FLAG: &str = "--autostart";

/// Pick the deep link out of the launch arguments (argv[0] already removed).
///
/// A "%1"-quoting break in the OS URL-scheme registration can smuggle extra argv
/// tokens, so apart from the known autostart flag the arguments must be exactly
/// one proxion:// URL of sane length with no whitespace, quotes or control chars.
fn parse_deep_link<I, S>(args: I) -> Option<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut found: Option<String> = None;
    for arg in args {
        let a = arg.as_ref();
        if a == AUTOSTART_FLAG {
            continue;
        }
        if found.is_some() {
            return None;
        }
        found = Some(a.to_string());
    }
    found.filter(|a| {
        a.starts_with("proxion://")
            && a.len() <= 2048
            && !a
                .chars()
                .any(|c| c.is_whitespace() || c.is_control() || c == '"' || c == '\'')
    })
}

fn focus_main_window(app: &AppHandle) {
    if let Some(win) = app.get_window("main") {
        win.show().ok();
        win.unminimize().ok();
        win.set_focus().ok();
    }
}

// ── Install kind + updater target ───────────────────────────────────────────

/// How this copy was installed. The Tauri v1 updater only knows the OS and
/// arch, so on its own it hands every Windows install the manifest's default
/// artifact (an NSIS user gets the MSI, a second install behind a UAC prompt)
/// and offers deb/rpm installs an update it cannot apply.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InstallKind {
    WindowsNsis,
    WindowsMsi,
    LinuxAppImage,
    /// deb or rpm: updated by the system package manager.
    LinuxPackage,
    Other,
}

fn install_kind(
    os: &str,
    exe_dir: &std::path::Path,
    appimage: Option<&std::ffi::OsStr>,
) -> InstallKind {
    match os {
        // The NSIS installer leaves uninstall.exe next to the app; MSI does not.
        "windows" if exe_dir.join("uninstall.exe").is_file() => InstallKind::WindowsNsis,
        "windows" => InstallKind::WindowsMsi,
        "linux" if appimage.map_or(false, |a| !a.is_empty()) => InstallKind::LinuxAppImage,
        "linux" => InstallKind::LinuxPackage,
        _ => InstallKind::Other,
    }
}

fn current_install_kind() -> InstallKind {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();
    install_kind(
        std::env::consts::OS,
        &exe_dir,
        std::env::var_os("APPIMAGE").as_deref(),
    )
}

/// The latest.json platform key for this install ("windows-x86_64-nsis"), from
/// the updater's default "<os>-<arch>". None keeps the default key.
fn updater_target_for(kind: InstallKind, base: Option<String>) -> Option<String> {
    let base = base?;
    match kind {
        InstallKind::WindowsNsis => Some(format!("{base}-nsis")),
        InstallKind::WindowsMsi => Some(format!("{base}-msi")),
        InstallKind::LinuxAppImage => Some(format!("{base}-appimage")),
        InstallKind::LinuxPackage | InstallKind::Other => None,
    }
}

fn self_update_supported(kind: InstallKind) -> bool {
    kind != InstallKind::LinuxPackage
}

#[tauri::command]
fn updates_supported() -> bool {
    self_update_supported(current_install_kind())
}

// ── proxion:// registration ───────────────────────────────────────────────────

const URL_SCHEME: &str = "proxion";

/// Tauri v1 cannot register a URL scheme at install time on Windows or Linux,
/// so the app points the scheme at its own executable on every launch. That
/// also follows the app when an update or reinstall moves it.
#[cfg(any(target_os = "windows", test))]
fn windows_scheme_command(exe: &str) -> Option<String> {
    if exe.is_empty() || exe.contains('"') || exe.chars().any(|c| c.is_control()) {
        return None;
    }
    Some(format!("\"{exe}\" \"%1\""))
}

#[cfg(target_os = "windows")]
fn register_url_scheme() {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let Some(cmd) = std::env::current_exe()
        .ok()
        .and_then(|e| e.to_str().and_then(windows_scheme_command))
    else {
        return;
    };
    let base = format!(r"HKCU\Software\Classes\{URL_SCHEME}");
    let open = format!(r"{base}\shell\open\command");
    let entries: [(&str, &str, &str); 3] = [
        (&base, "", "URL:Proxion Protocol"),
        (&base, "URL Protocol", ""),
        (&open, "", &cmd),
    ];
    for (key, name, value) in entries {
        let mut c = std::process::Command::new("reg");
        c.arg("add").arg(key);
        if name.is_empty() {
            c.arg("/ve");
        } else {
            c.arg("/v").arg(name);
        }
        c.args(["/t", "REG_SZ", "/d", value, "/f"])
            .creation_flags(CREATE_NO_WINDOW);
        if let Err(e) = c.output() {
            eprintln!("[deep-link] could not register {URL_SCHEME}://: {e}");
            return;
        }
    }
}

/// Quote a path for a .desktop Exec line, or None if it cannot be written safely.
#[cfg(any(target_os = "linux", test))]
fn desktop_exec_quote(path: &str) -> Option<String> {
    if path.is_empty() || path.chars().any(|c| c.is_control()) {
        return None;
    }
    let mut out = String::from("\"");
    for c in path.chars() {
        match c {
            // Exec quoting escapes these with a backslash, and the desktop-file
            // string escape then doubles every backslash.
            '"' | '`' | '$' => {
                out.push_str("\\\\");
                out.push(c);
            }
            '\\' => out.push_str("\\\\\\\\"),
            '%' => out.push_str("%%"),
            _ => out.push(c),
        }
    }
    out.push('"');
    Some(out)
}

#[cfg(any(target_os = "linux", test))]
fn desktop_handler_entry(exe: &str) -> Option<String> {
    Some(format!(
        "[Desktop Entry]\nType=Application\nName=Proxion\nExec={} %u\nTerminal=false\nNoDisplay=true\nMimeType=x-scheme-handler/{URL_SCHEME};\n",
        desktop_exec_quote(exe)?
    ))
}

#[cfg(target_os = "linux")]
fn register_url_scheme() {
    // An AppImage runs from a temporary mount; register the image itself.
    let exe = std::env::var("APPIMAGE")
        .ok()
        .filter(|a| !a.is_empty())
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|e| e.to_str().map(String::from))
        });
    let Some(entry) = exe.as_deref().and_then(desktop_handler_entry) else {
        return;
    };
    let apps = match std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()) {
        Some(d) => std::path::PathBuf::from(d).join("applications"),
        None => match std::env::var_os("HOME") {
            Some(h) => std::path::PathBuf::from(h).join(".local/share/applications"),
            None => return,
        },
    };
    let name = "proxion-url-handler.desktop";
    if std::fs::create_dir_all(&apps).is_err() || std::fs::write(apps.join(name), entry).is_err() {
        return;
    }
    let mime = format!("x-scheme-handler/{URL_SCHEME}");
    std::process::Command::new("xdg-mime")
        .args(["default", name, mime.as_str()])
        .output()
        .ok();
}

// macOS registers the scheme through Info.plist (CFBundleURLTypes).
#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn register_url_scheme() {}

// ── Autostart launch detection ────────────────────────────────────────────────

struct IsAutostartLaunch(bool);

#[tauri::command]
fn is_autostart_launch(state: State<IsAutostartLaunch>) -> bool {
    state.0
}

// ── Quit (clean shutdown) ─────────────────────────────────────────────────────

/// Ask the gateway to shut down and wait for it, killing it only if it does not
/// exit in time. A clean exit stops its cloudflared tunnel and flushes the
/// database; a bare kill skipped both, and on macOS/Linux it hit only the
/// onefile launcher and orphaned the real gateway process behind it.
/// If this app dies without calling it, the gateway sees EOF on its stdin and
/// shuts itself down (PROXION_SHUTDOWN_ON_STDIN_EOF).
fn shutdown_gateway(app: &AppHandle) {
    let child = app.state::<GatewayChild>().0.lock().unwrap().take();
    let Some(mut child) = child else { return };
    let flags = app.state::<GatewayFlags>();
    flags.stopping.store(true, Ordering::SeqCst);
    if child.write(b"shutdown\n").is_ok() {
        let deadline = Instant::now() + Duration::from_secs(8);
        while flags.running.load(Ordering::SeqCst) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    if flags.running.load(Ordering::SeqCst) {
        child.kill().ok();
    }
}

// ── Tray availability (Linux) ─────────────────────────────────────────────────

/// gdbus prints `(true,)` when the queried bus name has an owner.
#[cfg(any(target_os = "linux", test))]
fn name_has_owner_reply(out: &str) -> bool {
    out.trim() == "(true,)"
}

/// The Linux tray icon is a StatusNotifierItem, which only shows when a
/// StatusNotifierWatcher runs on the session bus. KDE Plasma always has one
/// (X11 and Wayland); GNOME only with an AppIndicator extension (Ubuntu ships
/// one, stock GNOME does not). Without it there is no icon to bring a hidden
/// window back, so the close button must quit instead. Checked on each close
/// so enabling the extension later takes effect.
#[cfg(target_os = "linux")]
fn tray_available() -> bool {
    std::process::Command::new("gdbus")
        .args([
            "call", "--session", "--timeout", "2",
            "--dest", "org.freedesktop.DBus",
            "--object-path", "/org/freedesktop/DBus",
            "--method", "org.freedesktop.DBus.NameHasOwner",
            "org.kde.StatusNotifierWatcher",
        ])
        .output()
        .map(|o| o.status.success() && name_has_owner_reply(&String::from_utf8_lossy(&o.stdout)))
        .unwrap_or(false)
}

#[cfg(not(target_os = "linux"))]
fn tray_available() -> bool {
    true
}

fn hide_main_window(app: &AppHandle) {
    if let Some(win) = app.get_window("main") {
        win.hide().ok();
    }
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    hide_main_window(&app);
    shutdown_gateway(&app);
    app.exit(0);
}

/// Stops the gateway before the updater replaces its executable (a running
/// gateway keeps the old binary locked and would outlive the update).
#[tauri::command]
async fn stop_gateway(app: AppHandle) {
    shutdown_gateway(&app);
}

// ── Gateway output ────────────────────────────────────────────────────────────

/// The reason from a "PROXION_GATEWAY_ERROR <kind> <detail>" line the gateway
/// prints when it cannot start (e.g. "port_in_use 8080").
fn parse_gateway_error(line: &str) -> Option<String> {
    line.trim()
        .strip_prefix("PROXION_GATEWAY_ERROR ")
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty() && s.len() <= 200)
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    let pending_link = parse_deep_link(std::env::args().skip(1));
    let is_autostart = std::env::args().any(|a| a == AUTOSTART_FLAG);

    let mut builder = tauri::Builder::default();
    if let Some(target) = updater_target_for(current_install_kind(), tauri::updater::target()) {
        builder = builder.updater_target(target);
    }

    builder
        // Registered first: a second launch forwards its argv here and exits during
        // plugin setup, before our setup could spawn another gateway sidecar.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            focus_main_window(app);
            if let Some(url) = parse_deep_link(argv.into_iter().skip(1)) {
                if let Some(win) = app.get_window("main") {
                    win.emit("deep-link-invoke", &url).ok();
                }
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_FLAG]),
        ))
        .manage(GatewayChild(Mutex::new(None)))
        .manage(GatewayFlags::default())
        .manage(PendingDeepLink(Mutex::new(pending_link)))
        .manage(TrayUnreadState(Mutex::new(vec![])))
        .manage(IsAutostartLaunch(is_autostart))
        .system_tray(build_tray())
        // The close button hides the window and the app keeps running in the tray,
        // so messages and calls still arrive. Quit in the tray menu stops it and
        // the gateway; launching Proxion again brings the window back. With no
        // tray to come back from (Linux without a StatusNotifierWatcher) the
        // close button quits.
        .on_window_event(|event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event.event() {
                if event.window().label() == "main" {
                    api.prevent_close();
                    event.window().hide().ok();
                    if !tray_available() {
                        let app = event.window().app_handle();
                        shutdown_gateway(&app);
                        app.exit(0);
                    }
                }
            }
        })
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
                "open" => {
                    if let Some(win) = app.get_window("main") {
                        win.show().ok();
                        win.set_focus().ok();
                    }
                }
                "quit" => {
                    hide_main_window(app);
                    shutdown_gateway(app);
                    app.exit(0);
                }
                id if id.starts_with("unread:") => {
                    let thread_id = id.strip_prefix("unread:").unwrap_or("");
                    if let Some(win) = app.get_window("main") {
                        win.show().ok();
                        win.set_focus().ok();
                        win.emit("navigate-to-thread", thread_id).ok();
                    }
                }
                _ => {}
            },
            SystemTrayEvent::LeftClick { .. } => {
                if let Some(win) = app.get_window("main") {
                    win.show().ok();
                    win.set_focus().ok();
                }
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            show_notification,
            store_pod_credentials,
            clear_pod_credentials,
            update_tray_unread,
            consume_pending_deep_link,
            is_autostart_launch,
            quit_app,
            stop_gateway,
            updates_supported,
        ])
        .setup(|app| {
            let app_data_dir = app
                .path_resolver()
                .app_data_dir()
                .expect("no app data dir available");
            std::fs::create_dir_all(&app_data_dir).ok();

            // Off the main thread: on Windows this shells out to reg.exe.
            if !cfg!(debug_assertions) {
                std::thread::spawn(register_url_scheme);
            }

            // Launched with --autostart: start in the tray, or minimized when
            // there is no tray to open it from.
            if app.state::<IsAutostartLaunch>().0 {
                if let Some(win) = app.get_window("main") {
                    if tray_available() {
                        win.hide().ok();
                    } else {
                        win.minimize().ok();
                    }
                }
            }

            // Load pod credentials from keychain to inject into gateway env
            let pod_css = kread("pod_css_url");
            let pod_email = kread("pod_email");
            let pod_pw = kread("pod_password");

            let app_handle = app.handle();

            match tauri::api::process::Command::new_sidecar("proxion-gateway") {
                Err(e) => {
                    eprintln!("[gateway] sidecar not found: {e}");
                    app_handle.emit_all("gateway-missing", ()).ok();
                }
                Ok(cmd) => {
                    // tauri 1.x Command has no per-var .env(); only .envs(HashMap).
                    let mut env: std::collections::HashMap<String, String> =
                        std::collections::HashMap::new();
                    env.insert("PROXION_HTTP_PORT".into(), "8080".into());
                    env.insert("PROXION_HOST".into(), "127.0.0.1".into());
                    env.insert("PROXION_WS_PORT".into(), "7474".into());
                    // The webview dials plain ws://127.0.0.1:7474 and cannot accept a
                    // self-signed cert, so the loopback sidecar stays on plain ws/http.
                    env.insert("PROXION_AUTO_TLS".into(), "0".into());
                    // Exit cleanly when this app closes our end of its stdin.
                    env.insert("PROXION_SHUTDOWN_ON_STDIN_EOF".into(), "1".into());
                    // After a relaunch or update, wait for the previous gateway to
                    // release its ports instead of failing on them.
                    env.insert("PROXION_BIND_WAIT".into(), "15".into());
                    env.insert(
                        "PROXION_DATA_DIR".into(),
                        app_data_dir.to_string_lossy().to_string(),
                    );
                    if let Some(v) = pod_css   { env.insert("PROXION_CSS_URL".into(),      v); }
                    if let Some(v) = pod_email  { env.insert("PROXION_CSS_EMAIL".into(),    v); }
                    if let Some(v) = pod_pw     { env.insert("PROXION_CSS_PASSWORD".into(), v); }
                    let cmd = cmd.envs(env);

                    let (mut rx, child) = cmd.spawn().expect("failed to spawn sidecar");
                    *app.state::<GatewayChild>().0.lock().unwrap() = Some(child);
                    app.state::<GatewayFlags>().running.store(true, Ordering::SeqCst);

                    let app_handle2 = app_handle.clone();
                    tauri::async_runtime::spawn(async move {
                        // Set once the gateway explained its own failure, so the
                        // generic crash toast does not repeat it.
                        let mut reported = false;
                        while let Some(event) = rx.recv().await {
                            match event {
                                tauri::api::process::CommandEvent::Stdout(line) => {
                                    if let Some(err) = parse_gateway_error(&line) {
                                        reported = true;
                                        app_handle2.emit_all("gateway-error", err).ok();
                                    }
                                    if line.contains("PROXION_GATEWAY_READY") {
                                        app_handle2.emit_all("gateway-ready", ()).ok();
                                        // Deliver cold-start deep link now that gateway is up
                                        let link = app_handle2
                                            .state::<PendingDeepLink>()
                                            .0.lock()
                                            .unwrap()
                                            .clone();
                                        if let Some(url) = link {
                                            app_handle2.emit_all("deep-link-invoke", &url).ok();
                                        }
                                    }
                                    println!("[gateway] {line}");
                                }
                                tauri::api::process::CommandEvent::Stderr(line) => {
                                    eprintln!("[gateway] ERR: {line}");
                                }
                                tauri::api::process::CommandEvent::Terminated(payload) => {
                                    eprintln!("[gateway] exited: {:?}", payload.code);
                                    let flags = app_handle2.state::<GatewayFlags>();
                                    flags.running.store(false, Ordering::SeqCst);
                                    let stopping = flags.stopping.load(Ordering::SeqCst);
                                    if payload.code.unwrap_or(0) != 0 && !reported && !stopping {
                                        app_handle2.emit_all("gateway-crashed", ()).ok();
                                    }
                                }
                                _ => {}
                            }
                        }
                    });
                }
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Closing the last window ends the event loop; stop the gateway with it.
            if let tauri::RunEvent::Exit = event {
                shutdown_gateway(app);
            }
        });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::{
        desktop_exec_quote, desktop_handler_entry, install_kind, name_has_owner_reply, parse_deep_link,
        parse_gateway_error, self_update_supported, updater_target_for,
        windows_scheme_command, InstallKind,
    };
    use std::ffi::OsStr;

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir()
            .join(format!("proxion-app-test-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn windows_install_kind_follows_uninstaller() {
        let msi = tmpdir("msi");
        assert_eq!(install_kind("windows", &msi, None), InstallKind::WindowsMsi);
        let nsis = tmpdir("nsis");
        std::fs::write(nsis.join("uninstall.exe"), b"").unwrap();
        assert_eq!(install_kind("windows", &nsis, None), InstallKind::WindowsNsis);
        std::fs::remove_dir_all(&msi).ok();
        std::fs::remove_dir_all(&nsis).ok();
    }

    #[test]
    fn linux_install_kind_follows_appimage_env() {
        let d = std::path::Path::new("/usr/bin");
        assert_eq!(
            install_kind("linux", d, Some(OsStr::new("/home/u/Proxion.AppImage"))),
            InstallKind::LinuxAppImage
        );
        assert_eq!(install_kind("linux", d, None), InstallKind::LinuxPackage);
        assert_eq!(install_kind("linux", d, Some(OsStr::new(""))), InstallKind::LinuxPackage);
        assert_eq!(install_kind("macos", d, None), InstallKind::Other);
    }

    #[test]
    fn tray_watcher_reply_is_parsed() {
        assert!(name_has_owner_reply("(true,)\n"));
        assert!(!name_has_owner_reply("(false,)\n"));
        assert!(!name_has_owner_reply(""));
        assert!(!name_has_owner_reply("Error: GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown"));
    }

    #[test]
    fn each_install_gets_its_own_manifest_key() {
        let win = || Some("windows-x86_64".to_string());
        assert_eq!(
            updater_target_for(InstallKind::WindowsNsis, win()).as_deref(),
            Some("windows-x86_64-nsis")
        );
        assert_eq!(
            updater_target_for(InstallKind::WindowsMsi, win()).as_deref(),
            Some("windows-x86_64-msi")
        );
        assert_eq!(
            updater_target_for(InstallKind::LinuxAppImage, Some("linux-x86_64".into())).as_deref(),
            Some("linux-x86_64-appimage")
        );
        assert_eq!(updater_target_for(InstallKind::Other, Some("darwin-aarch64".into())), None);
        assert_eq!(updater_target_for(InstallKind::WindowsNsis, None), None);
    }

    #[test]
    fn package_manager_installs_do_not_self_update() {
        assert!(!self_update_supported(InstallKind::LinuxPackage));
        assert!(self_update_supported(InstallKind::LinuxAppImage));
        assert!(self_update_supported(InstallKind::WindowsNsis));
        assert!(self_update_supported(InstallKind::WindowsMsi));
        assert!(self_update_supported(InstallKind::Other));
    }

    #[test]
    fn windows_scheme_command_quotes_exe_and_arg() {
        assert_eq!(
            windows_scheme_command(r"C:\Users\a b\AppData\Local\Proxion\Proxion.exe").as_deref(),
            Some(r#""C:\Users\a b\AppData\Local\Proxion\Proxion.exe" "%1""#)
        );
        assert_eq!(windows_scheme_command(r#"C:\a"b\x.exe"#), None);
        assert_eq!(windows_scheme_command(""), None);
    }

    #[test]
    fn desktop_exec_quoting() {
        assert_eq!(desktop_exec_quote("/usr/bin/proxion-app").as_deref(), Some(r#""/usr/bin/proxion-app""#));
        assert_eq!(desktop_exec_quote("/opt/a b/x").as_deref(), Some(r#""/opt/a b/x""#));
        assert_eq!(desktop_exec_quote("/o/50%/x").as_deref(), Some(r#""/o/50%%/x""#));
        assert_eq!(desktop_exec_quote("/o/$x").as_deref(), Some(r#""/o/\\$x""#));
        assert_eq!(desktop_exec_quote(r"/o/a\b").as_deref(), Some(r#""/o/a\\\\b""#));
        assert_eq!(desktop_exec_quote("/o/a\nb"), None);
        assert_eq!(desktop_exec_quote(""), None);
        let e = desktop_handler_entry("/home/u/Proxion.AppImage").unwrap();
        assert!(e.contains("Exec=\"/home/u/Proxion.AppImage\" %u\n"));
        assert!(e.contains("MimeType=x-scheme-handler/proxion;\n"));
    }

    #[test]
    fn gateway_error_line_is_parsed() {
        assert_eq!(
            parse_gateway_error("PROXION_GATEWAY_ERROR port_in_use 8080\r"),
            Some("port_in_use 8080".to_string())
        );
        assert_eq!(parse_gateway_error("PROXION_GATEWAY_READY"), None);
        assert_eq!(parse_gateway_error("PROXION_GATEWAY_ERROR "), None);
        assert_eq!(parse_gateway_error("  Web UI  : http://127.0.0.1:8080"), None);
    }

    #[test]
    fn accepts_single_valid_link() {
        assert_eq!(
            parse_deep_link(["proxion://join/abc123"]),
            Some("proxion://join/abc123".to_string())
        );
    }

    #[test]
    fn accepts_link_alongside_autostart_flag() {
        assert_eq!(
            parse_deep_link(["--autostart", "proxion://join/abc"]),
            Some("proxion://join/abc".to_string())
        );
    }

    #[test]
    fn rejects_smuggled_extra_token() {
        assert_eq!(parse_deep_link(["proxion://join/abc", "--evil"]), None);
        assert_eq!(parse_deep_link(["--evil", "proxion://join/abc"]), None);
    }

    #[test]
    fn rejects_two_links() {
        assert_eq!(parse_deep_link(["proxion://join/a", "proxion://join/b"]), None);
    }

    #[test]
    fn rejects_quote_space_and_control_chars() {
        assert_eq!(parse_deep_link(["proxion://join/a\"b"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a'b"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a b"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a\tb"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a\u{0}b"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a\u{1b}b"]), None);
        assert_eq!(parse_deep_link(["proxion://join/a\u{7f}b"]), None);
    }

    #[test]
    fn rejects_overlong() {
        let max = format!("proxion://{}", "a".repeat(2048 - 10));
        assert_eq!(parse_deep_link([max.as_str()]), Some(max.clone()));
        let over = format!("proxion://{}", "a".repeat(2048 - 9));
        assert_eq!(parse_deep_link([over.as_str()]), None);
    }

    #[test]
    fn rejects_non_proxion_scheme() {
        assert_eq!(parse_deep_link(["https://example.com"]), None);
        assert_eq!(parse_deep_link(["file:///etc/passwd"]), None);
        assert_eq!(parse_deep_link(["PROXION://join/a"]), None);
    }

    #[test]
    fn autostart_alone_or_no_args_is_none() {
        assert_eq!(parse_deep_link(["--autostart"]), None);
        assert_eq!(parse_deep_link(Vec::<String>::new()), None);
    }
}
