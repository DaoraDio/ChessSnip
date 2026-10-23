ChessSnip for Windows
====================

Run ChessSnip.exe. This is a standalone, single-file build; Python and other
ChessSnip runtime files do not need to be installed or copied alongside it.

Requirements: 64-bit Windows 10 or Windows 11.

- Click New or press Ctrl+Shift+X to capture the screen.
- Click the text-and-QR scan icon in the top toolbar to recognize both together.
  Detected text and QR areas are highlighted in the editor.
- Text recognition uses OCR language resources installed in Windows. Add a
  language in Windows Settings if OCR reports that no language is available.
- QR and chess recognition run locally. The chess model is included.
- To open directly in capture mode, start ChessSnip.exe --snip.

Keep THIRD_PARTY_NOTICES.txt, PyWinRT_LICENSE.txt, and ZXing_CPP_LICENSE.txt
with the executable when redistributing it.

Crash diagnostics are saved to %LOCALAPPDATA%\ChessSnip\Logs\crash.log.
