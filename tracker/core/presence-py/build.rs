//! The fingerprint of the sources this module is built of (the core, these bindings, the workspace's
//! manifests and lock file), readable as presence_core.SOURCE_HASH: an error report names the code that made
//! it also where no source comes along (the release wheel).

use std::path::{Path, PathBuf};

fn files(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(dir).expect("a source directory") {
        let path = entry.expect("an entry").path();
        if path.is_dir() {
            files(&path, out);
        } else if path.extension().is_some_and(|e| e == "rs") {
            out.push(path);
        }
    }
}

fn main() {
    let here = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets it"));
    let workspace = here.parent().expect("in the workspace").to_path_buf();
    let core = workspace.join("presence-core");
    let mut paths = Vec::new();
    for dir in [core.join("src"), here.join("src")] {
        println!("cargo:rerun-if-changed={}", dir.display());
        files(&dir, &mut paths);
    }
    for file in [
        core.join("Cargo.toml"),
        here.join("Cargo.toml"),
        workspace.join("Cargo.toml"),
        workspace.join("Cargo.lock"),
    ] {
        println!("cargo:rerun-if-changed={}", file.display());
        paths.push(file);
    }
    let mut named: Vec<(String, PathBuf)> = paths
        .into_iter()
        .map(|p| {
            let name = p
                .strip_prefix(&workspace)
                .unwrap_or(&p)
                .to_string_lossy()
                .replace('\\', "/");
            (name, p)
        })
        .collect();
    named.sort();
    let mut h = sha1_smol::Sha1::new();
    for (name, path) in &named {
        h.update(name.as_bytes());
        h.update(&std::fs::read(path).expect("a source file"));
    }
    println!(
        "cargo:rustc-env=PRESENCE_CORE_SOURCE_HASH={}",
        &h.digest().to_string()[..12]
    );
}
