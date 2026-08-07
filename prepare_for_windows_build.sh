#!/bin/bash
# Script to prepare minimal files for Windows build
# Usage: ./prepare_for_windows_build.sh

echo "Preparing minimal files for Windows build..."

# Create output directory
OUTPUT_DIR="Chinese_Video_Localization_AI_Windows_Build"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Copy essential files
echo "Copying essential files..."
cp -r src "$OUTPUT_DIR/"
cp requirements.txt "$OUTPUT_DIR/"
cp setup.py "$OUTPUT_DIR/"
cp build_exe.spec "$OUTPUT_DIR/"
cp build_exe.bat "$OUTPUT_DIR/"
cp build_nuitka.bat "$OUTPUT_DIR/"
cp BUILD_EXE_GUIDE.md "$OUTPUT_DIR/"
cp .env "$OUTPUT_DIR/" 2>/dev/null || cp .env.example "$OUTPUT_DIR/.env"
cp -r scripts "$OUTPUT_DIR/"

# Copy config if exists
if [ -d "config" ]; then
    cp -r config "$OUTPUT_DIR/"
fi

# Create empty directories that will be needed
mkdir -p "$OUTPUT_DIR/temp"
mkdir -p "$OUTPUT_DIR/local_data"

# Create README for Windows
cat > "$OUTPUT_DIR/README_WINDOWS.txt" << 'EOF'
CHINESE VIDEO LOCALIZATION AI - WINDOWS BUILD
==============================================

RECOMMENDED: Use Nuitka for better code protection
---------------------------------------------------

To build with Nuitka (BETTER SECURITY):
1. Install Visual C++ Build Tools:
   https://visualstudio.microsoft.com/visual-cpp-build-tools/
   Select "Desktop development with C++"

2. Open Command Prompt in this directory

3. Run: build_nuitka.bat

4. Wait for build to complete (20-60 minutes)

5. The executable will be: ChineseVideoLocalizationAI.exe

6. Configure:
   ren ChineseVideoLocalizationAI.env .env
   Edit .env and set WEB_SESSION_SECRET

ALTERNATIVE: PyInstaller (faster, lower security)
-------------------------------------------------

To build with PyInstaller:
1. Open Command Prompt in this directory
2. Run: build_exe.bat
3. Wait for build to complete (10-30 minutes)
4. The executable will be in: dist\ChineseVideoLocalizationAI\

Requirements:
- Windows 10/11
- Python 3.10 or higher
- 8GB+ RAM (16GB recommended)
- 10GB+ free disk space
- For Nuitka: Visual C++ Build Tools

For detailed instructions, see BUILD_EXE_GUIDE.md

CODE PROTECTION:
- PyInstaller: Low (easy to decompile)
- Nuitka: High (Python -> C -> machine code, very hard to decompile)
- Server: Absolute (code never leaves server)
EOF

echo "Done! Prepared files in: $OUTPUT_DIR/"
echo "Copy this folder to Windows and run build_exe.bat"
