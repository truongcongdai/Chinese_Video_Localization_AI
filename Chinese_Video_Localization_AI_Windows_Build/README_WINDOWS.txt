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
