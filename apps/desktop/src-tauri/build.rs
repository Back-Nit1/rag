fn main() {
    // Only these app commands exist as ACL permissions (allow-<name>); a window
    // gets them only through a capability file.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "health",
            "desktop_status",
            "desktop_connection",
        ]),
    ))
    .expect("tauri build");
}
