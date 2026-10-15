# Ready-to-run Windows builds

These builds are for 64-bit Windows 10 and 11. Python does not need to be installed.

## FastStart (recommended)

Run ChessSnip.exe in the ChessSnip-FastStart folder. Keep the _internal folder next to it and distribute the entire ChessSnip-FastStart folder together.

## Single-file standalone

Run Standalone/ChessSnip.exe. It is self-contained; Python and additional ChessSnip runtime files are not required. It may take longer to start because it extracts its runtime at launch.

Both builds support launching directly into screen capture with the --snip argument. The chess recognition model is included. Keep the corresponding THIRD_PARTY_NOTICES.txt with redistributed copies.
