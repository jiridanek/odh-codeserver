"""Hatchling custom build hook for odh-codeserver.

Drives the code-server npm build pipeline during ``pip wheel``, producing a
wheel that contains the release-standalone tree.

In production (Konflux / cachi2), all npm deps are prefetched at
``/cachi2/output/deps/npm/`` and ``npm ci --offline`` is used.

When built by AIPCC's fromager, the sdist must already contain the npm
dependency cache (pre-packed tarball), because fromager runs ``build_wheel``
under ``unshare --net`` (no network access).
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


CODESERVER_VERSION = "v4.106.3"


class CustomBuildHook(BuildHookInterface):
    """Build hook that compiles code-server and packs the result into the wheel."""

    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return

        root = Path(self.root)

        self._log(f"code-server version : {CODESERVER_VERSION}")
        self._log(f"build root          : {root}")
        self._log(f"platform            : {platform.machine()}")

        source_code, source_prefetch = self._locate_sources(root)
        self._log(f"source_code         : {source_code}")
        self._log(f"source_prefetch     : {source_prefetch}")

        if not source_prefetch.is_dir():
            self._log(
                "WARNING: code-server source not found. "
                "Creating a placeholder wheel for testing."
            )
            self._create_placeholder(root)
            self._set_platform_tag(build_data)
            return

        self._run_apply_patch(source_code, source_prefetch)
        self._run_npm_ci(source_code, source_prefetch)
        self._run_npm_build(source_code, source_prefetch)
        self._run_npm_build_vscode(source_code, source_prefetch)
        self._run_npm_release(source_code, source_prefetch)
        self._run_npm_release_standalone(source_code, source_prefetch)

        data_dir = root / "odh_codeserver" / "data"
        self._copy_release_standalone(source_prefetch, data_dir)
        self._set_platform_tag(build_data)

    # ------------------------------------------------------------------
    # Source location
    # ------------------------------------------------------------------

    def _locate_sources(self, root: Path) -> tuple[Path, Path]:
        """Return (source_code, source_prefetch) paths."""
        env_code = os.environ.get("CODESERVER_SOURCE_CODE")
        env_prefetch = os.environ.get("CODESERVER_SOURCE_PREFETCH")
        if env_code and env_prefetch:
            return Path(env_code), Path(env_prefetch)

        # Standalone repo: sources shipped alongside pyproject.toml
        prefetch = root / "prefetch-input" / "code-server"
        if prefetch.is_dir():
            return root, prefetch

        # In notebooks mono-repo: sibling directory
        sibling = root.parent / "ubi9-python-3.12"
        if sibling.is_dir():
            return sibling, sibling / "prefetch-input" / "code-server"

        # Sources not found -- caller handles the fallback
        return root, root / "prefetch-input" / "code-server"

    # ------------------------------------------------------------------
    # Build steps
    # ------------------------------------------------------------------

    def _run_apply_patch(self, source_code: Path, source_prefetch: Path) -> None:
        self._log("Applying patches ...")
        patches_dir = source_code / "prefetch-input" / "patches"
        version_overlay = patches_dir / f"code-server-{CODESERVER_VERSION}"
        if version_overlay.is_dir():
            shutil.copytree(version_overlay, source_prefetch, dirs_exist_ok=True)

        target_patches = source_code / "patches"
        target_patches.mkdir(parents=True, exist_ok=True)
        for script in ("setup-offline-binaries.sh", "codeserver-offline-env.sh", "tweak-gha.sh"):
            src = patches_dir / script
            if src.exists():
                shutil.copy2(src, target_patches / script)

        apply_script = patches_dir / "apply-patch.sh"
        if apply_script.exists():
            shutil.copy2(apply_script, source_code / "apply-patch.sh")
            env = self._build_env(source_code, source_prefetch)
            # Create a no-op gcc-toolset-14 shim if it doesn't exist (non-RHEL hosts).
            # apply-patch.sh sources /opt/rh/gcc-toolset-14/enable unconditionally.
            gcc_shim = Path("/opt/rh/gcc-toolset-14/enable")
            if not gcc_shim.exists():
                self._log("gcc-toolset-14 not found, creating no-op shim")
                gcc_shim.parent.mkdir(parents=True, exist_ok=True)
                gcc_shim.write_text("# no-op shim for non-RHEL hosts
")
            self._shell(f"cd {source_code} && bash ./apply-patch.sh", env=env)

    def _run_npm_ci(self, source_code: Path, source_prefetch: Path) -> None:
        """Install npm deps. Always offline -- network is not available."""
        self._log("npm ci --offline ...")
        env = self._build_env(source_code, source_prefetch)
        self._shell(
            f"cd {source_code} && "
            f"source ./patches/setup-offline-binaries.sh && "
            f"cd {source_prefetch} && "
            f"CI=1 npm ci --offline",
            env=env,
        )

    def _run_npm_build(self, source_code: Path, source_prefetch: Path) -> None:
        self._log("npm run build ...")
        env = self._build_env(source_code, source_prefetch)
        self._shell(
            f". {source_code}/patches/codeserver-offline-env.sh && "
            f"cd {source_prefetch} && npm run build",
            env=env,
        )

    def _run_npm_build_vscode(self, source_code: Path, source_prefetch: Path) -> None:
        ver = CODESERVER_VERSION.lstrip("v")
        self._log(f"npm run build:vscode (VERSION={ver}) ...")
        env = self._build_env(source_code, source_prefetch)
        self._shell(
            f". {source_code}/patches/codeserver-offline-env.sh && "
            f"cd {source_prefetch} && VERSION={ver} npm run build:vscode",
            env=env,
        )

    def _run_npm_release(self, source_code: Path, source_prefetch: Path) -> None:
        self._log("npm run release ...")
        env = self._build_env(source_code, source_prefetch)
        self._shell(
            f". {source_code}/patches/codeserver-offline-env.sh && "
            f"export KEEP_MODULES=1 && cd {source_prefetch} && npm run release",
            env=env,
        )

    def _run_npm_release_standalone(self, source_code: Path, source_prefetch: Path) -> None:
        self._log("npm run release:standalone ...")
        env = self._build_env(source_code, source_prefetch)
        self._shell(
            f". {source_code}/patches/codeserver-offline-env.sh && "
            f"export KEEP_MODULES=1 && cd {source_prefetch} && npm run release:standalone",
            env=env,
        )

    # ------------------------------------------------------------------
    # Post-build
    # ------------------------------------------------------------------

    def _copy_release_standalone(self, source_prefetch: Path, data_dir: Path) -> None:
        release_dir = source_prefetch / "release-standalone"
        if not release_dir.is_dir():
            raise FileNotFoundError(
                f"release-standalone not found at {release_dir}. "
                "The npm build may have failed."
            )
        self._log(f"Copying {release_dir} -> {data_dir}")
        if data_dir.exists():
            shutil.rmtree(data_dir)
        shutil.copytree(release_dir, data_dir, symlinks=True)

        nfpm_script = source_prefetch / "ci" / "build" / "code-server-nfpm.sh"
        if nfpm_script.exists():
            dest = data_dir / "bin" / "code-server-wrapper.sh"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(nfpm_script, dest)

        self._fix_permissions(data_dir)

    @staticmethod
    def _fix_permissions(data_dir: Path) -> None:
        for name in ("bin/code-server", "bin/code-server-wrapper.sh", "lib/node"):
            exe = data_dir / name
            if exe.exists():
                exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _create_placeholder(self, root: Path) -> None:
        """Create a minimal data dir so hatchling can build a placeholder wheel."""
        data_dir = root / "odh_codeserver" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "PLACEHOLDER").write_text(
            "This is a placeholder wheel. The real wheel requires the code-server "
            "source tree with pre-fetched npm dependencies.\n"
        )

    # ------------------------------------------------------------------
    # Platform tag
    # ------------------------------------------------------------------

    @staticmethod
    def _set_platform_tag(build_data: dict) -> None:
        machine = platform.machine()
        arch_map = {"x86_64": "x86_64", "aarch64": "aarch64", "ppc64le": "ppc64le", "s390x": "s390x"}
        arch = arch_map.get(machine, machine)
        build_data["tag"] = f"py3-none-manylinux_2_28_{arch}"
        build_data["pure_python"] = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_env(self, source_code: Path, source_prefetch: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["CODESERVER_SOURCE_CODE"] = str(source_code)
        env["CODESERVER_SOURCE_PREFETCH"] = str(source_prefetch)
        env.setdefault("HOME", "/root")
        # Point HERMETO_OUTPUT at the sdist-bundled cachi2 deps if present,
        # so setup-offline-binaries.sh and codeserver-offline-env.sh find
        # the prefetched npm tarballs, ripgrep wheel, etc.
        root = Path(self.root)
        bundled_cachi2 = root / "cachi2" / "output"
        if bundled_cachi2.is_dir():
            env["HERMETO_OUTPUT"] = str(bundled_cachi2)
        return env

    def _shell(self, cmd: str, *, env: dict[str, str] | None = None) -> None:
        self._log(f"  $ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")
        result = subprocess.run(["bash", "-c", cmd], env=env, stdout=sys.stdout, stderr=sys.stderr)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd[:200]}")

    def _log(self, msg: str) -> None:
        sys.stderr.write(f"[odh-codeserver] {msg}\n")
        sys.stderr.flush()
