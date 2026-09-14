# Publishing NobaMacro

## GitHub source and release

1. Sign in as `JOINTMASTER1` and verify your email if you have not already done so.
2. Open https://github.com/new and create a public repository named `NobaMacro` under `JOINTMASTER1`. The intended repository address is `https://github.com/JOINTMASTER1/NobaMacro` (create it first; this guide does not establish that it exists). Leave the automatic README, license and gitignore options off because these files are included here.
3. Extract this ZIP. Upload the **contents inside NobaMacro**, so `README.md`, `LICENSE`, `launch.sh` and the Python files appear at the repository root. Include the hidden `.github` folder and `.gitignore`; using Git is the most reliable way to include them. Do not upload personal recordings, logs or saved preferences.
4. This bundle includes MIT credited to Joint. Confirm that choice before publishing. MIT permits others to modify, sell and redistribute the code, including closed-source versions, while preserving its copyright and license notices. It does not require forks to retain the visible footer. GPL-3.0 is an alternative if you want distributed derivatives to remain open source; decide before the first public release.
5. Test the update on Nobara: change each key, save, restart, start recording from another app, stop with the chosen key, and play three repeats. Test F9 and the Stop button, then record a second macro. Check both Settings and Recorder tabs and the credit at the bottom. Try a key already assigned in KDE and select a different key if it conflicts.
6. Once verified, create a GitHub release tagged `v2.4` and attach the ZIP as a convenient download. GitHub also produces source archives from the tag. Describe the supported desktop and known limits accurately.

The included GitHub Actions workflow runs portable tests. It does not validate physical devices, KDE shortcuts, policy authentication or pointer positioning.

## Flatpak and Flathub: additional development required

A ZIP is not a Flatpak. This version invokes a privileged evdev/uinput helper through `pkexec` and talks directly to KWin scripting. Those host assumptions need an explicit design for Flatpak's device, process and D-Bus restrictions. Merely wrapping the launch script or granting device visibility does not supply host administrator privileges.

A future port should evaluate desktop portals for input capture, remote-desktop input injection and global shortcuts, including whether the target KDE version supports the required capture behavior. Alternatively, a separately installed host helper needs a defined authorization and installation model. This is architectural work and requires testing; no working Flatpak manifest is included or implied.

After that work, packaging needs:

- A stable application ID, for example `io.github.JOINTMASTER1.NobaMacro` for this GitHub account. This is a proposed ID for the future port, not an existing published Flatpak.
- A reproducible Flatpak build manifest, chosen runtime and bundled dependencies.
- A desktop entry, application icon and AppStream metadata with license, description, releases and real screenshot/source links.
- Minimal justified sandbox permissions, clean installation/update testing on KDE, and a Flathub submission if you want it listed there.

Publish and test the native source release first. A Nobara/Fedora RPM is another possible packaging route for the existing host-oriented implementation.

References: [GitHub account setup](https://docs.github.com/en/account-and-profile/how-tos/account-management/creating-an-account-on-github), [MIT](https://choosealicense.com/licenses/mit/), [Flatpak sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html), [Flathub requirements](https://docs.flathub.org/docs/for-app-authors/requirements), [Flathub submission](https://docs.flathub.org/docs/for-app-authors/submission).

## Support link

The README links to https://ko-fi.com/jointmaster. Include `.github/FUNDING.yml` on your repository's default branch to configure the Sponsor button for the same Ko-fi account. If it is hidden, enable **Settings > General > Features > Sponsorships**. No GitHub Sponsors enrollment is needed for this external Ko-fi link.

Reference: [GitHub sponsor button documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/displaying-a-sponsor-button-in-your-repository).
