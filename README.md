<p align="center">
  <img src="ChessSnip.png" alt="ChessSnip logo" width="180">
</p>

# ChessSnip

ChessSnip is a Windows Snipping Tool clone with its most-used capture and editing features, plus an offline chess-position recognizer that helps turn a board screenshot into PGN.

## Features

- Capture a region of the screen, then annotate, crop, copy, or save it.
- Use drawing tools, shapes, a color picker, and image zoom.
- Recognize a chessboard locally and review the detected pieces, board orientation, side to move, and castling rights before exporting PGN.
- Optionally open the position in Lichess.

Chess recognition uses the included ONNX model and runs on the local computer. ChessSnip does not need to upload screenshots for recognition.

## Ready-to-run Windows builds (no Python needed)

Prebuilt Windows builds are included in the release/ folder:

- release/Standalone/ChessSnip.exe is a single-file build. Run it directly; Python is not required.
- release/ChessSnip-FastStart/ is the faster-starting build. Run ChessSnip.exe inside that folder and keep its _internal/ folder beside it. Share the complete folder.

Both builds include the chess recognition model. The standalone build takes longer to open because it unpacks its bundled runtime at launch; the FastStart build loads its files in place.

## Requirements

- Windows 10 or 11.
- Python 3.10 or newer to run from source.
- A standard Windows Python installation with Tcl/Tk.
- The Python packages in requirements.txt.

## Run from source

From this folder, install dependencies and start the app:

    py -m pip install -r requirements.txt
    pyw .\ChessSnip.pyw

To start directly in capture mode:

    pyw .\ChessSnip.pyw --snip

## Build Windows distributions

Install the build dependencies:

    py -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt

Build a single-file executable:

    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm .\ChessSnip.spec

Build the fast-start folder distribution:

    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm .\ChessSnipFast.spec

PyInstaller writes generated files under build/ and dist/. The single-file build produces dist/ChessSnip.exe. The fast-start build produces a dist/ChessSnip/ folder; distribute that entire folder, including its internal dependency files.

## Repository files

- ChessSnip.pyw — main Windows application.
- chess_recognition.py — chessboard and piece recognition.
- chess_position_classifier.onnx — bundled local piece classifier.
- ChessSnip.png and ChessSnip.ico — application icons.
- ChessSnip.spec and ChessSnipFast.spec — portable PyInstaller build definitions.
- THIRD_PARTY_NOTICES.txt — attribution and license notice for the bundled chess model and adapted algorithm.
- release/ — prebuilt, ready-to-run Windows distributions that do not require Python to be installed.

## License

ChessSnip is licensed under the MIT License. See [LICENSE](LICENSE) for the full license text. Third-party model and algorithm notices are in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
