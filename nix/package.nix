{
  lib,
  stdenv,
  rustPlatform,
  cmake,
  python3,
  llvmPackages,
}:

rustPlatform.buildRustPackage {
  pname = "caesar";
  version = (lib.importTOML ../Cargo.toml).package.version;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../.cargo
      ../Cargo.toml
      ../Cargo.lock
      ../build.rs
      ../src
      ../tests
      ../jani
      ../scooter
      ../z3rro
    ];
  };

  cargoLock = {
    lockFile = ../Cargo.lock;
    # z3.rs is a git dependency so that its z3 submodule (the bundled Z3
    # sources) is fetched. Update this hash when its rev in Cargo.toml changes.
    outputHashes."z3-0.13.2" = "sha256-wRYeRnT60hO0iCrMYkuJwiH8IzCmpGotMQQxxjOHRYc=";
  };

  # Only the caesar binary, not the scooter workspace member.
  cargoBuildFlags = [
    "--package"
    "caesar"
  ];

  strictDeps = true;

  nativeBuildInputs = [
    # z3-sys builds the bundled Z3 (default `static-link-z3` feature)
    cmake
    python3
    # z3-sys generates its bindings with bindgen, which needs libclang
    rustPlatform.bindgenHook
  ]
  # .cargo/config.toml links with -fuse-ld=lld on x86_64-linux; the wrapped
  # lld sets the Nix dynamic linker and rpath
  ++ lib.optional (stdenv.hostPlatform.system == "x86_64-linux") llvmPackages.bintools;

  # cmake is only needed by z3-sys's build script, not for configuring caesar
  dontUseCmakeConfigure = true;

  # Several integration tests rely on 30 s solver timeouts and fail
  # spuriously on slow or busy builders; run `cargo test` in the dev shell.
  doCheck = false;

  meta = {
    description = "Deductive verifier for probabilistic programs";
    homepage = "https://www.caesarverifier.org/";
    license = lib.licenses.mit;
    mainProgram = "caesar";
  };
}
