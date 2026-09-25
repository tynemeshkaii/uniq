# Testing Video Uniqualizer On macOS

This build is intended for internal testing.

The app bundle is self-contained, but it is not notarized yet. On another Mac, the first launch may be blocked by Gatekeeper until the tester manually confirms it.

## What You Should Receive

- `Video Uniqualizer.dmg`
- `Video Uniqualizer.dmg.sha256`

## Verify The Download

Open Terminal in the folder that contains the files and run:

```bash
shasum -a 256 "Video Uniqualizer.dmg"
```

Compare the printed hash with the value in `Video Uniqualizer.dmg.sha256`.

## Install The App

1. Open `Video Uniqualizer.dmg`
2. Drag `Video Uniqualizer.app` into `Applications`
3. Open the `Applications` folder

## First Launch On Another Mac

Try this first:

1. Right click `Video Uniqualizer.app`
2. Choose `Open`
3. Confirm the launch if macOS asks again

If macOS still blocks the app:

1. Open `System Settings`
2. Go to `Privacy & Security`
3. Find the blocked app message near the bottom
4. Click `Open Anyway`
5. Launch the app again

## Requirements

- Apple Silicon Mac (M1 or newer)
- The macOS version printed as "Requires" in `RELEASE_NOTES.txt`

## Reporting A Problem

Inside the app, use **Help → Report a Problem…**. Describe what happened and
click *Create Report*. A file named `VideoUniqualizer-report-<date>.zip`
appears on your Desktop — send that file back. It contains the app's log,
your settings and crash traces, never your media. Untick *Include the app
log* if the names of the files you processed are confidential.

If the app crashed or was force-quit, it offers to create this report the
next time you open it.

The *Open GitHub Issue* button opens a public issue page; only use it if you
are fine with the description being public.

## Getting New Betas

Use **Help → Check for Updates…**, or allow the automatic daily check when
the app asks on first launch. It only contacts GitHub's public release list.

## If The App Does Not Open

Please send back:

- the exact macOS error message
- the macOS version
- whether the app was opened from the DMG or from `Applications`
- whether `Right click -> Open` or `Open Anyway` was already tried

## Do Not Do These Things

- Do not disable Gatekeeper globally
- Do not run random security override commands from the internet
- Do not change SIP settings
