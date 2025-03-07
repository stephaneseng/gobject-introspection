@echo on
call "C:\Program Files (x86)\Microsoft Visual Studio\2017\Community\VC\Auxiliary\Build\vcvarsall.bat" x64
@echo on

py -3 -c "import urllib.request, sys; urllib.request.urlretrieve(*sys.argv[1:])" "https://github.com/lexxmark/winflexbison/releases/download/v2.5.14/win_flex_bison-2.5.14.zip" win_flex_bison.zip
py -3 -m zipfile -e win_flex_bison.zip win_flex_bison

py -3 -c "import urllib.request, sys; urllib.request.urlretrieve(*sys.argv[1:])" "https://nirbheek.in/files/binaries/pkg-config/win32/pkg-config.exe" pkg-config.exe

SET PATH=%CD%;%CD%\win_flex_bison;%PATH%

pip3 install --upgrade --user meson==0.49.0 || goto :error
meson _build || goto :error
ninja -C _build || goto :error

meson test -C _build --suite=gobject-introspection || goto :error

goto :EOF
:error
exit /b 1
