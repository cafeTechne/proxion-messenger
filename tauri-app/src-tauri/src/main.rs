// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use tauri::{
    AppHandle, CustomMenuItem, Manager, State, SystemTray, SystemTrayEvent,
    SystemTrayMenu, SystemTrayMenuItem,
};
use tauri_plugin_autostart::MacosLauncher;
use serde::{Deserialize, Serialize};

// ── Shared state ─────────────────────────────────────────────────────────────

struct GatewayChild(Mutex<Option<tauri::api::process::CommandChild>>);
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

#[tauri::command]
fn store_pod_credentials(css_url: String, email: String, password: String) -> Result<(), String> {
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

// ── Autostart launch detection ────────────────────────────────────────────────

struct IsAutostartLaunch(bool);

#[tauri::command]
fn is_autostart_launch(state: State<IsAutostartLaunch>) -> bool {
    state.0
}

// ── Quit (clean shutdown) ─────────────────────────────────────────────────────

#[tauri::command]
fn quit_app(app: AppHandle, gateway: State<GatewayChild>) {
    if let Some(child) = gateway.0.lock().unwrap().take() {
        child.kill().ok();
    }
    app.exit(0);
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    let pending_link = parse_deep_link(std::env::args().skip(1));
    let is_autostart = std::env::args().any(|a| a == AUTOSTART_FLAG);

    tauri::Builder::default()
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
        .manage(PendingDeepLink(Mutex::new(pending_link)))
        .manage(TrayUnreadState(Mutex::new(vec![])))
        .manage(IsAutostartLaunch(is_autostart))
        .system_tray(build_tray())
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::MenuItemClick { id, .. } => match id.as_str() {
                "open" => {
                    if let Some(win) = app.get_window("main") {
                        win.show().ok();
                        win.set_focus().ok();
                    }
                }
                "quit" => {
                    if let Some(child) =
                        app.state::<GatewayChild>().0.lock().unwrap().take()
                    {
                        child.kill().ok();
                    }
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
        ])
        .setup(|app| {
            let app_data_dir = app
                .path_resolver()
                .app_data_dir()
                .expect("no app data dir available");
            std::fs::create_dir_all(&app_data_dir).ok();

            // Hide window if launched with --autostart
            if app.state::<IsAutostartLaunch>().0 {
                if let Some(win) = app.get_window("main") {
                    win.hide().ok();
                }
            }

            // Load pod credentials from keychain to inject into gateway env
            let pod_css = kentry("pod_css_url").ok().and_then(|e| e.get_password().ok());
            let pod_email = kentry("pod_email").ok().and_then(|e| e.get_password().ok());
            let pod_pw = kentry("pod_password").ok().and_then(|e| e.get_password().ok());

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

                    let app_handle2 = app_handle.clone();
                    tauri::async_runtime::spawn(async move {
                        while let Some(event) = rx.recv().await {
                            match event {
                                tauri::api::process::CommandEvent::Stdout(line) => {
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
                                    if payload.code.unwrap_or(0) != 0 {
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
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::parse_deep_link;

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
