# Internal macOS Distribution Design

Date: 2026-04-22
Project: Video Uniqualizer
Scope: Safe internal distribution for testers without Apple Developer signing/notarization

## Goal

Provide a reliable way to share `Video Uniqualizer.app` with testers on macOS before the project has an Apple Developer account, Developer ID certificate, and notarization pipeline.

The result should produce shareable artifacts, verify bundle integrity, and give testers clear first-launch instructions without using unsafe system-wide Gatekeeper bypasses.

## Non-Goals

- Public-ready macOS distribution
- Developer ID signing
- Apple notarization
- Eliminating Gatekeeper warnings on other Macs
- Migrating the project from PyInstaller to Xcode

## Current Problem

The project can now build a working `.app`, but without Apple Developer credentials the resulting app is not accepted by Gatekeeper on external machines. This makes the app unsuitable for normal public distribution even when the bundle itself is technically valid.

The project also needs clearer packaging and tester guidance so internal sharing is predictable and support burden is low.

## Recommended Approach

Implement an internal distribution workflow with two explicit modes:

1. `local` build for development and personal use
2. `share` build for sending to testers

Both modes reuse the same app build. The `share` mode adds packaging, validation, checksums, and tester-facing instructions.

## Why This Approach

- It is honest about the current security/distribution limits.
- It avoids pretending ad-hoc signatures are equivalent to notarization.
- It reduces tester friction with a standard package and clear instructions.
- It prepares the project for a later upgrade to Developer ID + notarization without rewriting the whole build script.

## Build Workflow

### Local Mode

`./build.sh`

Behavior:

- Build the `.app`
- Perform bundle integrity checks required for local confidence
- Produce the app in `dist/`

### Share Mode

`./build.sh share`

Behavior:

- Build the `.app`
- Run validation checks
- Create a distributable DMG
- Generate a SHA-256 checksum file
- Generate a short release notes text file for the sender/tester
- Print a concise summary of what to send and what Gatekeeper behavior to expect

## Validation Requirements

The build script should automatically run the following checks before reporting success:

1. `codesign --verify --deep --strict` on the `.app`
2. Broken symlink detection inside the app bundle
3. Smoke test for launching the app executable
4. DMG creation success in `share` mode

`spctl` should be treated as informational only in the non-notarized workflow. The script should report that rejection on external systems is expected until Developer ID notarization is added.

## Distribution Artifacts

The `share` workflow should generate these files in `dist/`:

- `Video Uniqualizer.app`
- `Video Uniqualizer.dmg`
- `Video Uniqualizer.dmg.sha256`
- `RELEASE_NOTES.txt`

## Tester Instructions

Add a dedicated tester document:

- `TESTING_ON_MAC.md`

It should explain:

- how to open the DMG
- how to drag the app into `Applications`
- how to launch a non-notarized app safely on macOS
- how to use `Right click -> Open`
- how to use `Privacy & Security -> Open Anyway` if needed
- how to report launch failures and where to find the delivered artifact name

The instructions must not recommend:

- disabling Gatekeeper globally
- changing SIP
- using dangerous security overrides

## Build Script Changes

`build.sh` should be refactored to:

- accept an optional mode argument
- default to `local`
- support `share`
- keep the current bundle-fix behavior that preserves Qt frameworks
- centralize validation steps into reusable shell functions
- produce a release summary only for `share`

If the mode is unsupported, the script should fail fast with a short usage message.

## Release Notes Content

The generated `RELEASE_NOTES.txt` should contain:

- app name and build date
- included artifacts
- SHA-256 verification command
- short tester install steps
- a clear note that the app is not notarized yet, so first launch on another Mac may require manual confirmation

## Error Handling

The workflow should stop with a non-zero exit code when:

- PyInstaller build fails
- codesign verification fails
- broken symlinks are found
- the smoke launch test fails
- DMG generation fails in `share` mode

Warnings should be non-fatal only for:

- `spctl` rejection caused by missing notarization
- cosmetic font warnings during smoke launch

## Testing Strategy

Implementation should be validated with:

1. Fresh local `./build.sh`
2. Fresh `./build.sh share`
3. Verification that expected files exist in `dist/`
4. Verification that the app launches locally after the share build
5. Verification that checksum and tester docs are readable and consistent with produced artifact names

## Future Upgrade Path

When Apple Developer access becomes available, this workflow should evolve rather than be replaced:

- keep `local` mode
- keep `share` mode semantics
- add a later `release` mode for Developer ID signing and notarization
- reuse the same validation layer before and after notarization

## Acceptance Criteria

The work is complete when:

1. `./build.sh` still produces a working local `.app`
2. `./build.sh share` produces a DMG, checksum file, and release notes
3. The build script fails on broken bundle state instead of silently succeeding
4. Tester instructions exist and avoid unsafe macOS guidance
5. The output clearly explains that Gatekeeper approval is not expected until notarization is implemented
