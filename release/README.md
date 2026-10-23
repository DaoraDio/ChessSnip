# Ready-to-run Windows builds

These builds are for 64-bit Windows 10 and 11. Python does not need to be installed.

## FastStart (recommended)

Run ChessSnip.exe in the ChessSnip-FastStart-OCR-QR folder. Keep the _internal folder next to it and distribute the entire ChessSnip-FastStart-OCR-QR folder together. It includes an offline text-recognition engine and models, loaded the first time you click the toolbar scan icon.

## Single-file standalone

Run Standalone-OCR-QR/ChessSnip.exe. It is self-contained; Python and additional ChessSnip runtime files are not required. It may take longer to start because it extracts its runtime at launch.

Both builds support launching directly into screen capture with the --snip argument. The FastStart build uses bundled OCR models; the single-file build uses the OCR languages installed in Windows. The toolbar scan icon recognizes text and QR codes together, and detected areas are highlighted in the editor. Text, QR, and chess recognition run locally. The chess model is included. Keep the included third-party notices and license files with redistributed copies.

ChessSnip writes diagnostic logs to `%LOCALAPPDATA%\ChessSnip\Logs\crash.log`. The log records unhandled errors and Python thread traces for fatal native crashes when available. It does not store screenshot contents or recognized chess positions.
