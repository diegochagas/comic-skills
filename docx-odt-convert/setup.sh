#!/usr/bin/env bash
# docx-odt-convert setup (also run by <repo>/setup.sh): nothing to install in
# the repo - it only checks for LibreOffice and its Python binding (uno), which
# the script uses through the SYSTEM python3. Safe to re-run.
set -euo pipefail
command -v soffice >/dev/null 2>&1 || echo "WARNING: soffice (LibreOffice) not found - sudo apt install libreoffice-writer"
/usr/bin/python3 -c "import uno" 2>/dev/null || echo "WARNING: python3-uno not found - sudo apt install python3-uno"
command -v gio >/dev/null 2>&1 || echo "WARNING: gio not found - --trash will not work (libglib2.0-bin)"
echo "docx-odt-convert: $(soffice --version 2>/dev/null | head -1)"
