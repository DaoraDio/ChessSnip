# Ready-to-run Windows builds

These builds are for 64-bit Windows 10 and 11. Python does not need to be installed.

## FastStart (recommended)

Run ChessSnip.exe in the ChessSnip-FastStart-OCR-QR folder. Keep the _internal folder next to it and distribute the entire ChessSnip-FastStart-OCR-QR folder together. Text recognition uses Windows' built-in OCR API and the language resources installed on the PC; no OCR models are bundled.

## Single-file standalone

Run Standalone-OCR-QR/ChessSnip.exe. It is self-contained; Python and additional ChessSnip runtime files are not required. It may take longer to start because it extracts its runtime at launch.

Both builds support launching directly into screen capture with the --snip argument. The toolbar scan icon recognizes text and QR codes together, and detected areas are highlighted in the editor. Both builds use Windows OCR language resources; add a language in Windows Settings if OCR is unavailable. Text, QR, and chess recognition run locally. The chess model is included. Keep the included third-party notices and license files with redistributed copies.

ChessSnip writes diagnostic logs to `%LOCALAPPDATA%\ChessSnip\Logs\crash.log`. The log records unhandled errors and Python thread traces for fatal native crashes when available. It does not store screenshot contents or recognized chess positions.
