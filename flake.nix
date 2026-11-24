{
  description = "Caesar, a deductive verifier for probabilistic programs";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      eachSystem =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
          "x86_64-darwin"
          "aarch64-darwin"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # `nix build`, `nix run . -- <args>`
      packages = eachSystem (pkgs: rec {
        caesar = pkgs.callPackage ./nix/package.nix { };
        default = caesar;
      });

      # `nix develop`: the same tools the installation guide asks other
      # distributions for (Rust, Python, CMake, Clang, LLD), so that plain
      # `cargo build` / `cargo test` work as documented.
      devShells = eachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.cargo
            pkgs.rustc
            pkgs.rustfmt
            pkgs.clippy
            pkgs.rust-analyzer
            # z3-sys builds the bundled Z3 and generates bindings with bindgen
            pkgs.cmake
            pkgs.python3
            pkgs.rustPlatform.bindgenHook
          ]
          # .cargo/config.toml links with -fuse-ld=lld on x86_64-linux
          ++ pkgs.lib.optional (
            pkgs.stdenv.hostPlatform.system == "x86_64-linux"
          ) pkgs.llvmPackages.bintools;
          RUST_SRC_PATH = "${pkgs.rustPlatform.rustLibSrc}";
        };
      });
    };
}
